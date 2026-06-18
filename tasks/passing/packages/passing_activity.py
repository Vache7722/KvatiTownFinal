"""Passing maneuver state machine.

The Duckiebot lane-follows until an obstacle (duckie or duckiebot) blocks
its lane. It slows down behind it, measures the obstacle's speed from the
camera, pulls out into the opposing lane, passes, and merges back.

State flow:

    LANE_FOLLOW -> APPROACH -> PULL_OUT -> PASSING -> MERGE -> LANE_FOLLOW

While PASSING, lane following runs on the horizontally mirrored camera
image (the opposing lane looks exactly like the normal lane in a mirror),
and the resulting wheel commands are swapped back.
"""

import time
from typing import List, Optional, Tuple

from tasks.passing.packages.speed_estimation import SpeedEstimator, distance_from_bbox

Detection = Tuple[Tuple[int, int, int, int], float, int]

LANE_FOLLOW = 'LANE_FOLLOW'
APPROACH    = 'APPROACH'
PULL_OUT    = 'PULL_OUT'
PASSING     = 'PASSING'
MERGE       = 'MERGE'

_DEFAULTS = {
    # Obstacle triggers, as fractions of the detector's img_size.
    'approach_y2':        0.30,   # bbox bottom past this -> start APPROACH
    'pullout_y2':         0.42,   # bbox bottom past this -> commit to the pass
    'emergency_y2':       0.55,   # safety net: full stop if something is this close
    'lane_min_x':         0.32,   # detections left of this are in the opposing lane
    'lane_min_x_close':   0.18,   # ...but the window widens as the obstacle nears
    'oncoming_min_x':     0.05,   # oncoming-traffic guard watches this left band
    'lane_max_x':         0.95,
    'emergency_min_x':    0.10,   # the emergency stop watches a wider window
    'straight_cmd_diff':  0.12,   # only pull out when lane steering is this straight
    'pullout_force_timeout': 6.0, # but never wait longer than this once ready
    'max_commit_distance': 0.45,  # trail closer than this before starting a pass
    'commit_cx_min':      0.42,   # target must sit centered in view at commit —
    'commit_cx_max':      0.72,   # a target drifting sideways is cornering ahead
    'blind_hold':         2.5,    # s to hold after a close obstacle leaves the view
    # Approach behavior
    'approach_speed_scale': 0.75, # slow down immediately when APPROACH begins
    'follow_speed_scale': 0.40,   # slowest speed multiplier while trailing
    'measure_timeout':    3.0,    # max seconds to wait for a speed estimate
    # Open-loop lane change S-curves (each phase lasts *_time seconds).
    # Gentler is better: same ~0.2 m lateral shift at a lower yaw rate.
    'pull_out_time':      1.05,
    'pull_out_steer':     0.20,
    'merge_time':         1.00,
    'merge_steer':        0.16,
    'maneuver_speed':     0.22,
    'moving_pass_boost':  1.35,   # passing speed multiplier for moving targets   # wheel command level during the arcs
    # Passing in the opposing lane
    'pass_clear_len':     0.25,   # extra meters past the obstacle before merging
    'moving_extra_clear': 0.30,   # additional margin for moving targets (the
                                  # speed estimate biases low at close range)
    # Eager moving classification: calling a parked bot "moving" just makes
    # the pass slightly longer; calling a mover "parked" causes a collision.
    'stationary_speed_thresh': 0.02,
    'pass_min_time':      1.2,
    'pass_max_time':      8.0,
    # A second vehicle queued behind the pass target (occluded at commit
    # time) shows up ahead in the home lane mid-pass; its speed can't be
    # measured anymore, so assume it crawls at this rate when sizing the
    # extended pass.
    'convoy_speed_assumption': 0.12,
    'clear_frames':       4,      # consecutive obstacle-free frames before MERGE
    'repass_time':        3.5,    # extra passing seconds after an aborted merge
    'min_rel_speed':      0.04,   # m/s floor for the time-to-pass division
    'speed_gain':         1.0,    # m/s of real speed per unit wheel command
}


class PassingStateMachine:

    def __init__(self, config: dict = None, speed_estimator: SpeedEstimator = None):
        cfg = dict(_DEFAULTS)
        cfg.update(config or {})
        self.cfg = cfg

        self.estimator = speed_estimator or SpeedEstimator(
            speed_gain=cfg['speed_gain'])

        self.state           = LANE_FOLLOW
        self._state_since    = time.monotonic()
        self._pass_duration  = cfg['pass_max_time']
        self._clear_count    = 0
        self._abort_count    = 0
        self._moving_pass    = False
        self._convoy         = False
        self._last_ymax      = 0.0   # last ymax seen while approaching
        self._lost_since: Optional[float] = None
        self.target_speed:    Optional[float] = None  # frozen at pull-out
        self.target_distance: Optional[float] = None
        self.last_measurement: Optional[float] = None  # last completed pass

    def reset(self):
        self.estimator.reset()
        self.state           = LANE_FOLLOW
        self._state_since    = time.monotonic()
        self._clear_count    = 0
        self._last_ymax      = 0.0
        self._lost_since     = None
        self.target_speed    = None
        self.target_distance = None

    # ------------------------------------------------------------------

    def _enter(self, state: str, now: float):
        self.state        = state
        self._state_since = now

    def _elapsed(self, now: float) -> float:
        return now - self._state_since

    def _obstacle_ahead(self, detections: List[Detection], img_size: int):
        """Return the closest detection inside our lane, or None.

        The left edge of the lane window widens with proximity: a close
        vehicle slightly off our axis appears at a wide angle, and losing
        track of it during catch-up is how collisions happen.
        """
        cfg  = self.cfg
        a_px = cfg['approach_y2']  * img_size
        e_px = cfg['emergency_y2'] * img_size
        best = None
        for det in detections:
            (xmin, _, xmax, ymax), _, _ = det
            cx   = (xmin + xmax) / 2.0
            frac = min(1.0, max(0.0, (ymax - a_px) / max(1.0, e_px - a_px)))
            min_x = cfg['lane_min_x'] - frac * (cfg['lane_min_x'] - cfg['lane_min_x_close'])
            if not (img_size * min_x <= cx <= img_size * cfg['lane_max_x']):
                continue
            if best is None or ymax > best[0][3]:
                best = det
        return best

    def _oncoming(self, detections: List[Detection], img_size: int) -> bool:
        """True when something occupies the opposing lane ahead — do not
        pull out into it."""
        cfg = self.cfg
        for (x1, _, x2, y2), _, _ in detections:
            cx = (x1 + x2) / 2.0
            if (img_size * cfg['oncoming_min_x'] <= cx < img_size * cfg['lane_min_x']
                    and y2 >= img_size * cfg['approach_y2']):
                return True
        return False

    def _near_ymax(self, detections: List[Detection], img_size: int) -> float:
        """Largest bbox bottom in the wide emergency window. Catches obstacles
        that slipped left of the lane window at close range."""
        ys = [y2 for (x1, _, x2, y2), _, _ in detections
              if (x1 + x2) / 2.0 >= img_size * self.cfg['emergency_min_x']]
        return max(ys, default=0.0)

    # ------------------------------------------------------------------

    def step(self,
             detections: List[Detection],
             img_size: int,
             normal_cmds: Tuple[float, float],
             mirror_cmds: Tuple[float, float],
             now: float = None,
             is_curve: bool = False,
             curve_evidence: bool = False,
             long_straight: bool = False) -> Tuple[float, float]:
        """Advance the state machine one frame and return wheel commands.

        normal_cmds    -- lane-following commands for the current frame
        mirror_cmds    -- lane-following commands computed on the flipped frame
        is_curve       -- sticky curve flag; passing is deferred while set
        curve_evidence -- actual curving observed just now; allows the
                          mid-pass bailout merge
        long_straight  -- straight road proven for a sustained stretch; moving
                          targets are only passed then (they cannot be seen
                          while alongside, so a corner mid-pass is a collision)
        """
        if now is None:
            now = time.monotonic()
        cfg = self.cfg

        obstacle = self._obstacle_ahead(detections, img_size)
        ymax     = obstacle[0][3] if obstacle else 0.0

        if self.state == LANE_FOLLOW:
            if obstacle and ymax >= img_size * cfg['approach_y2']:
                self.estimator.reset()
                self._enter(APPROACH, now)
            # Safety net for anything that got close without triggering APPROACH
            # (e.g. an obstacle that drifted left of the lane window).
            if self._near_ymax(detections, img_size) >= img_size * cfg['emergency_y2']:
                return 0.0, 0.0
            return normal_cmds

        if self.state == APPROACH:
            ready = self.estimator.confident or self._elapsed(now) > cfg['measure_timeout']

            if not obstacle:
                # The camera sits at roof height of the other bot: inside
                # ~0.3 m the obstacle leaves the view entirely. If it was
                # close when it vanished, it is in the blind spot dead
                # ahead — pass it, or hold until the estimate is ready.
                if self._last_ymax >= img_size * cfg['pullout_y2']:
                    if self._lost_since is None:
                        self._lost_since = now
                    # In the blind spot the lines are occluded too, so judge
                    # by recent curve evidence rather than the sticky flag.
                    runway_ok = long_straight or not self._target_moving()
                    if (ready and runway_ok and not curve_evidence
                            and not self._oncoming(detections, img_size)):
                        self._commit_pull_out(now)
                        return self._arc(-1, 'pull_out_steer')
                    if now - self._lost_since < cfg['blind_hold']:
                        return 0.0, 0.0
                elif self._last_ymax >= img_size * cfg['approach_y2']:
                    # Dropout at medium range: the obstacle is still somewhere
                    # ahead, the detector just lost it for a moment (flicker,
                    # or a stalled frame). Creep instead of resuming full
                    # speed; give up only after the hold.
                    if self._lost_since is None:
                        self._lost_since = now
                    if now - self._lost_since < cfg['blind_hold']:
                        return (normal_cmds[0] * cfg['follow_speed_scale'],
                                normal_cmds[1] * cfg['follow_speed_scale'])
                # Far away or held too long without an estimate -> resume.
                self._enter(LANE_FOLLOW, now)
                self._last_ymax  = 0.0
                self._lost_since = None
                return normal_cmds

            self._last_ymax  = ymax
            self._lost_since = None
            left, right = normal_cmds

            # Trail the obstacle: drop speed immediately on approach, blend
            # down further with proximity, stop if dangerously close.
            span = max(1e-6, (cfg['pullout_y2'] - cfg['approach_y2']) * img_size)
            frac = min(1.0, max(0.0, (ymax - cfg['approach_y2'] * img_size) / span))
            scale = (cfg['approach_speed_scale']
                     - frac * (cfg['approach_speed_scale'] - cfg['follow_speed_scale']))
            if self._near_ymax(detections, img_size) >= img_size * cfg['emergency_y2']:
                left = right = scale = 0.0

            # Feed the estimator the speed we actually command this frame.
            self.target_distance = self.estimator.update(
                obstacle[0], img_size, (left + right) / 2.0 * scale, now)

            # Never start a pass mid-curve: trail the obstacle until the road
            # is straight. But a close obstacle occludes the lane lines and
            # keeps the sticky flag "unproven" forever — so after the timeout,
            # commit as long as no actual curving was observed recently.
            straight = ((not is_curve
                         and abs(normal_cmds[0] - normal_cmds[1]) < cfg['straight_cmd_diff'])
                        or (self._elapsed(now) > cfg['pullout_force_timeout']
                            and not curve_evidence))
            close_enough = (self.target_distance or 1.0) <= cfg['max_commit_distance']
            # A target sliding out of the central band is turning into a
            # corner ahead of us — passing would chase it off the straight.
            # At point-blank range the check is meaningless (centimeters of
            # offset become huge angles), so it only applies farther out.
            (oxmin, _, oxmax, _), _, _ = obstacle
            ocx = (oxmin + oxmax) / 2.0
            target_centered = (ymax >= img_size * cfg['emergency_y2']
                               or img_size * cfg['commit_cx_min'] <= ocx
                               <= img_size * cfg['commit_cx_max'])
            # Moving targets need a long proven straight: they cannot be
            # seen while alongside, so the whole pass must fit before the
            # next corner.
            runway_ok = long_straight or not self._target_moving()
            if (ready and straight and close_enough and target_centered
                    and runway_ok
                    and not self._oncoming(detections, img_size)
                    and ymax >= img_size * cfg['pullout_y2']):
                self._commit_pull_out(now)
                return self._arc(-1, 'pull_out_steer')
            return left * scale, right * scale

        if self.state == PULL_OUT:
            # S-curve: arc left, then counter-arc right so the heading is
            # straight again once we are inside the opposing lane.
            e = self._elapsed(now)
            if e < cfg['pull_out_time']:
                return self._arc(-1, 'pull_out_steer')
            if e < 2 * cfg['pull_out_time']:
                return self._arc(+1, 'pull_out_steer')
            self._enter(PASSING, now)
            return self._swap(mirror_cmds)

        if self.state == PASSING:
            # The obstacle must be out of the right half of the view before
            # we are allowed to merge back.
            blockers = [
                bbox for bbox, _, _ in detections
                if (bbox[0] + bbox[2]) / 2.0 >= img_size * 0.5
                and bbox[3] >= img_size * cfg['approach_y2']
            ]
            self._clear_count = 0 if blockers else self._clear_count + 1

            if blockers:
                # Something is ahead in the home lane mid-pass — either the
                # pass target itself or a second vehicle that was queued
                # behind it, occluded at commit time. It will slip into the
                # close-range blind spot before the merge, so only this
                # timer keeps the bot in the opposing lane long enough:
                # extend the pass to overtake the farthest blocker at a
                # conservative assumed speed, and overtake boosted.
                self._moving_pass = True
                dist  = max(distance_from_bbox(b, img_size,
                                               self.estimator.vfov_deg,
                                               self.estimator.obstacle_height_m)
                            for b in blockers)
                v_ego = cfg['maneuver_speed'] * self._pass_boost() * cfg['speed_gain']
                v_rel = max(cfg['min_rel_speed'],
                            v_ego - cfg['convoy_speed_assumption'])
                need  = self._elapsed(now) + (dist + cfg['pass_clear_len']
                                              + cfg['moving_extra_clear']) / v_rel
                if need > self._pass_duration:
                    self._convoy        = True
                    self._pass_duration = min(need, cfg['pass_max_time'])

            done = (self._elapsed(now) >= self._pass_duration
                    and self._clear_count >= cfg['clear_frames'])
            # A curve arriving mid-pass: get back into our lane as soon as
            # the right side is clear rather than riding into a blind corner.
            # Demands fresh curve evidence (not just the sticky flag) and a
            # longer clear streak than a normal merge — detections flicker
            # during the maneuver and a false bailout merges early.
            # Never during a convoy pass: the unfinished obstacle is blind
            # just ahead in the home lane and bailing out merges onto it.
            curve_bailout = (curve_evidence
                             and not self._convoy
                             and self._elapsed(now) >= cfg['pass_min_time']
                             and self._clear_count >= 2 * cfg['clear_frames'])
            if done or curve_bailout or self._elapsed(now) >= cfg['pass_max_time']:
                self._enter(MERGE, now)
                return self._arc(+1, 'merge_steer')
            return self._swap(mirror_cmds)

        if self.state == MERGE:
            # If the obstacle shows up on the right mid-merge, we merged too
            # early (it was hidden alongside us) — go back to passing.
            blocking = any(
                ((x1 + x2) / 2.0 >= img_size * 0.5) and y2 >= img_size * cfg['pullout_y2']
                for (x1, _, x2, y2), _, _ in detections
            )
            # Cap the aborts: endless PASSING<->MERGE ping-pong zigzags the
            # bot off the road; after two attempts just finish the merge and
            # let APPROACH deal with whatever is still there.
            if blocking and self._abort_count < 2:
                self._abort_count  += 1
                self._pass_duration = cfg['repass_time']
                self._clear_count   = 0
                self._enter(PASSING, now)
                return self._swap(mirror_cmds)
            e = self._elapsed(now)
            if e < cfg['merge_time']:
                return self._arc(+1, 'merge_steer')
            if e < 2 * cfg['merge_time']:
                return self._arc(-1, 'merge_steer')
            self.last_measurement = self.target_speed
            self._enter(LANE_FOLLOW, now)
            return normal_cmds

        return normal_cmds

    # ------------------------------------------------------------------

    def _target_moving(self) -> bool:
        return (self.estimator.latest_speed or 0.0) > self.cfg['stationary_speed_thresh']

    def _pass_boost(self) -> float:
        """Moving targets are passed faster so the maneuver ends sooner."""
        return self.cfg['moving_pass_boost'] if self._moving_pass else 1.0

    def _commit_pull_out(self, now: float):
        self.target_speed   = self.estimator.latest_speed or 0.0
        self._moving_pass   = self.target_speed > self.cfg['stationary_speed_thresh']
        self._pass_duration = self._compute_pass_duration()
        self._clear_count   = 0
        self._abort_count   = 0
        self._convoy        = False
        self._last_ymax     = 0.0
        self._lost_since    = None
        self._enter(PULL_OUT, now)

    def _compute_pass_duration(self) -> float:
        """Time in the opposing lane: close the gap to the obstacle plus a
        clearance margin, at the relative speed of the maneuver.

        The pull-out S-curve itself already covers ground toward the
        obstacle before the PASSING timer starts, so that relative travel
        is subtracted — otherwise the bot merges back far too late."""
        cfg    = self.cfg
        v_ego  = cfg['maneuver_speed'] * self._pass_boost() * cfg['speed_gain']
        v_tgt  = self.target_speed or 0.0
        v_rel  = max(cfg['min_rel_speed'], v_ego - v_tgt)
        gap    = (self.target_distance or 0.5) + cfg['pass_clear_len']
        if self._moving_pass:
            gap += cfg['moving_extra_clear']
        # ~90% of the S-curve arc length projects onto forward travel.
        gap   -= (0.9 * cfg['maneuver_speed'] * cfg['speed_gain'] - v_tgt) * 2 * cfg['pull_out_time']
        t      = gap / v_rel
        return min(cfg['pass_max_time'], max(cfg['pass_min_time'], t))

    def _arc(self, direction: int, steer_key: str) -> Tuple[float, float]:
        """Open-loop arc segment. direction -1 = left, +1 = right."""
        v = self.cfg['maneuver_speed']
        s = self.cfg[steer_key]
        if direction < 0:
            return v * (1.0 - s), v * (1.0 + s)
        return v * (1.0 + s), v * (1.0 - s)

    def _swap(self, cmds: Tuple[float, float]) -> Tuple[float, float]:
        """Mirror-world commands map back to reality with sides swapped,
        sped up when overtaking a moving target."""
        left, right = cmds
        boost = self._pass_boost()
        return min(1.0, right * boost), min(1.0, left * boost)
