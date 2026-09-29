"""Fault injection for the arm-only planned trajectory outcome boundary."""

from robot_sdk.wb import WholeBodyAPI, WholeBodyError


class _Arm:
    def __init__(self, *, fail=False, converge=True):
        self.fail = fail
        self.converge = converge
        self.sent = []

    def send_joint_position(self, target, *, blocking):
        if self.fail:
            raise RuntimeError("injected arm transport failure")
        self.sent.append(list(target))

    def get_state(self):
        return {"q": self.sent[-1] if self.converge else [0.0] * 7}


class _Base:
    def __init__(self):
        self.calls = []

    def execute_action(self, *target):
        self.calls.append(target)

    def get_state(self):
        return {"base_pose": self.calls[-1]}


def _execute(arm):
    base = _Base()
    wb = WholeBodyAPI(arm, base, command_rate_hz=1000,
                      settle_timeout=0.01, arm_only_settle_timeout=0.01)
    waypoint = [0.0, 0.0, 0.0] + [0.5] * 7 + [0.0] * 6
    return wb, base, waypoint


def test_arm_only_success_holds_planned_base_pose():
    arm = _Arm()
    wb, base, waypoint = _execute(arm)
    wb._execute_trajectory([waypoint], timeout=1.0, mask="arm_only")
    assert arm.sent
    assert base.calls and all(call == (0.0, 0.0, 0.0) for call in base.calls)


def test_arm_only_transport_failure_is_not_swallowed():
    wb, base, waypoint = _execute(_Arm(fail=True))
    try:
        wb._execute_trajectory([waypoint], timeout=1.0, mask="arm_only")
    except WholeBodyError as exc:
        assert "injected arm transport failure" in str(exc)
    else:
        raise AssertionError("arm transport failure was swallowed")
    assert base.calls == [(0.0, 0.0, 0.0)]


def test_arm_only_nonconvergence_is_not_reported_complete():
    wb, base, waypoint = _execute(_Arm(converge=False))
    try:
        wb._execute_trajectory([waypoint], timeout=1.0, mask="arm_only")
    except WholeBodyError as exc:
        assert "did not converge" in str(exc)
    else:
        raise AssertionError("arm nonconvergence was reported complete")
    assert base.calls and all(call == (0.0, 0.0, 0.0) for call in base.calls)


def test_arm_only_rejects_a_plan_that_moves_base():
    wb, base, waypoint = _execute(_Arm())
    moved = [0.01, 0.0, 0.0] + waypoint[3:]
    try:
        wb._execute_trajectory([waypoint, moved], timeout=1.0, mask="arm_only")
    except WholeBodyError as exc:
        assert "changes the base target" in str(exc)
    else:
        raise AssertionError("moving-base arm-only trajectory was accepted")
    assert base.calls == []


def test_arm_only_base_hold_failure_is_not_swallowed():
    wb, base, waypoint = _execute(_Arm())
    def fail(*target):
        raise RuntimeError("injected base transport failure")
    base.execute_action = fail
    try:
        wb._execute_trajectory([waypoint], timeout=1.0, mask="arm_only")
    except WholeBodyError as exc:
        assert "Arm-only base hold failed" in str(exc)
    else:
        raise AssertionError("base hold failure was swallowed")


def test_arm_only_requires_three_stable_joint_and_base_samples():
    class FluctuatingArm(_Arm):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def get_state(self):
            self.reads += 1
            value = 0.49 if self.reads == 2 else 0.5
            return {"q": [value] * 7}

    arm = FluctuatingArm()
    wb, base, waypoint = _execute(arm)
    wb._execute_trajectory([waypoint], timeout=1.0, mask="arm_only")
    assert arm.reads >= 5

    wb, base, waypoint = _execute(_Arm())
    base.get_state = lambda: {"base_pose": [0.03, 0.0, 0.0]}
    try:
        wb._execute_trajectory([waypoint], timeout=1.0, mask="arm_only")
    except WholeBodyError as exc:
        assert "did not converge" in str(exc)
    else:
        raise AssertionError("drifting base was reported settled")
