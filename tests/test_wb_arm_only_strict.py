"""Fault injection for the arm-only planned trajectory outcome boundary."""

from robot_sdk.wb import WholeBodyAPI, WholeBodyError


class _Arm:
    def __init__(self, *, fail=False, converge=True, residual=0.0):
        self.fail = fail
        self.converge = converge
        self.residual = residual
        self.sent = []

    def send_joint_position(self, target, *, blocking):
        if self.fail:
            raise RuntimeError("injected arm transport failure")
        self.sent.append(list(target))

    def get_state(self):
        return {"q": [x - self.residual for x in self.sent[-1]]
                if self.converge else [0.0] * 7}


class _Base:
    def __init__(self):
        self.calls = []

    def execute_action(self, *target):
        self.calls.append(target)


def _execute(arm):
    base = _Base()
    wb = WholeBodyAPI(arm, base, command_rate_hz=1000,
                      settle_timeout=0.01)
    waypoint = [0.0, 0.0, 0.0] + [0.5] * 7 + [0.0] * 6
    return wb, base, waypoint


def test_arm_only_success_skips_base_commands():
    arm = _Arm()
    wb, base, waypoint = _execute(arm)
    wb._execute_trajectory([waypoint], timeout=1.0, mask="arm_only")
    assert arm.sent
    assert base.calls == []


def test_arm_only_transport_failure_is_not_swallowed():
    wb, base, waypoint = _execute(_Arm(fail=True))
    try:
        wb._execute_trajectory([waypoint], timeout=1.0, mask="arm_only")
    except WholeBodyError as exc:
        assert "injected arm transport failure" in str(exc)
    else:
        raise AssertionError("arm transport failure was swallowed")
    assert base.calls == []


def test_arm_only_nonconvergence_is_not_reported_complete():
    wb, base, waypoint = _execute(_Arm(converge=False))
    try:
        wb._execute_trajectory([waypoint], timeout=1.0, mask="arm_only")
    except WholeBodyError as exc:
        assert "did not converge" in str(exc)
    else:
        raise AssertionError("arm nonconvergence was reported complete")
    assert base.calls == []


def test_arm_only_one_centiradian_residual_is_not_complete():
    wb, base, waypoint = _execute(_Arm(residual=0.01))
    try:
        wb._execute_trajectory([waypoint], timeout=1.0, mask="arm_only")
    except WholeBodyError as exc:
        assert "did not converge" in str(exc)
    else:
        raise AssertionError("one centiradian residual was reported complete")
    assert base.calls == []
