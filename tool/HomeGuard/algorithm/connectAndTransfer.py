# -*- coding:utf-8 -*-
from z3 import *
import re
import threading

_DEFAULT_TEST_DATA = {"rules": [], "conditions": [], "actions": []}
_THREAD_STATE = threading.local()

_NUMBER_RE = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$')


def _init_state():
    if getattr(_THREAD_STATE, "initialized", False):
        return
    _THREAD_STATE.initialized = True
    _THREAD_STATE.test_data = _DEFAULT_TEST_DATA
    _THREAD_STATE.z3_var_cache = {}
    _THREAD_STATE.z3_ctx = Context()


def set_runtime_state(test_data=None):
    _init_state()
    _THREAD_STATE.test_data = test_data or _DEFAULT_TEST_DATA
    _THREAD_STATE.z3_var_cache = {}
    _THREAD_STATE.z3_ctx = Context()


def clear_runtime_state():
    set_runtime_state(_DEFAULT_TEST_DATA)


def _get_test_data():
    _init_state()
    return _THREAD_STATE.test_data


def get_z3_context():
    _init_state()
    return _THREAD_STATE.z3_ctx


def new_solver():
    return Solver(ctx=get_z3_context())


def true_expr():
    return BoolVal(True, ctx=get_z3_context())


def false_expr():
    return BoolVal(False, ctx=get_z3_context())


def _is_number(val) -> bool:
    if val is None:
        return False
    s = str(val).strip()
    if s == '':
        return False
    return _NUMBER_RE.fullmatch(s) is not None


def _get_var(var_name: str):
    _init_state()
    v = _THREAD_STATE.z3_var_cache.get(var_name)
    if v is not None:
        return v
    # 统一使用 Real，避免同一设备属性在不同规则中出现 Int/Real 混用时产生 sort 冲突。
    v = Real(var_name, ctx=get_z3_context())
    _THREAD_STATE.z3_var_cache[var_name] = v
    return v


def getAllRules(data_source=None, userId=''):
    """获取所有规则"""
    return _get_test_data()['rules']


def getCondition(Cid, data_source=None):
    """根据条件ID获取条件"""
    for condition in _get_test_data()['conditions']:
        if str(condition[0]) == str(Cid):
            return condition
    return None


def getAction(Aid, data_source=None):
    """根据动作ID获取动作"""
    for action in _get_test_data()['actions']:
        if str(action[0]) == str(Aid):
            return action
    return None


def getActbyDev(Did, data_source=None):
    """根据设备ID获取动作"""
    Did = str(Did)
    result = []
    for action in _get_test_data()['actions']:
        if str(action[1]) == Did:
            result.append(action)
    return result if result else None


def getActbyAttr(a1, a2, a3, data_source=None):
    """根据设备ID、属性和新值获取动作ID"""
    result = []
    for action in _get_test_data()['actions']:
        if (
            str(action[1]) == str(a1)
            and str(action[2]) == str(a2)
            and str(action[3]) == str(a3)
        ):
            result.append((action[0],))
    return result


def getRule(Rid, data_source=None):
    """根据规则ID获取规则"""
    for rule in _get_test_data()['rules']:
        if str(rule[0]) == str(Rid):
            return rule
    return None


def conditionToZ3(condition):
    """将条件转换为Z3表达式"""
    if condition is None or condition is True:
        return true_expr()

    # 条件格式：[conditionId, deviceId, attribute, compareType, standardValue]
    deviceId = condition[1]
    attribute = condition[2]
    compareType = condition[3]
    standardValue = condition[4]

    if not _is_number(standardValue):
        raise ValueError(f"Condition value is not numeric: {standardValue!r}")

    var_name = f"device_{deviceId}_{attribute}"
    var = _get_var(var_name)
    std_val = RealVal(str(standardValue).strip(), ctx=get_z3_context())

    if compareType == '1':  # 等于
        return var == std_val
    elif compareType == '2':  # 大于
        return var > std_val
    elif compareType == '3':  # 小于
        return var < std_val
    elif compareType == '4':  # 大于等于
        return var >= std_val
    elif compareType == '5':  # 小于等于
        return var <= std_val
    elif compareType == '6':  # 不等于
        return var != std_val
    else:
        return true_expr()


def actionToZ3(action):
    """将动作转换为Z3表达式"""
    if action is None or action is True:
        return true_expr()

    # 动作格式：[actionId, deviceId, attribute, newValue]
    deviceId = action[1]
    attribute = action[2]
    newValue = action[3]

    if not _is_number(newValue):
        raise ValueError(f"Action value is not numeric: {newValue!r}")

    var_name = f"device_{deviceId}_{attribute}"
    var = _get_var(var_name)
    new_val = RealVal(str(newValue).strip(), ctx=get_z3_context())

    return var == new_val


def ConditionsImplie(con1, con2, data_source=None):
    """检查条件蕴含关系"""
    c1z = conditionToZ3(getCondition(con1))
    c2z = conditionToZ3(getCondition(con2))

    solver = new_solver()
    solver.add(Not(Implies(c1z, c2z)))
    return solver.check() == unsat


def getPolicy(data_source=None):
    """获取策略（模拟实现）"""
    return []


def getEffect(data_source=None):
    """获取效果（模拟实现）"""
    return []
