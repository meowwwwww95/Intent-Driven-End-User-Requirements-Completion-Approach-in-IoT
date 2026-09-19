# -*- coding:utf-8 -*- 
from z3 import *
import connectAndTransfer as cat
import time
from functools import reduce

dayofweeks = '1,2,3,4,5,6,7'
starttime = '00:00:00'
endtime = '12:59:59'


def traverseTable(db, userId=''):
    actDict = {}
    atFlag = 0
    result = []
    rule = cat.getAllRules(db, userId)
    # ['ruleId', 'ruleName', 'conditionIds', 'actionIds']
    s = cat.new_solver()
    for r in rule:
        act = cat.getAction(r[3], db)
        actKey = str(act[1]) + '+' + str(act[2]) + '+' + str(act[3])
        try:
            actDict[actKey]
        except:
            actDict[actKey] = 1
            actIdSet = []
            ruleSet = []
            sameAct = cat.false_expr()
            actIds = cat.getActbyAttr(act[1], act[2], act[3], db)
            for a in actIds:
                a = str(a[0])
                actIdSet.append(a)
            for rl in rule:
                # 检查时间约束：如果规则没有时间约束(None)或者时间约束匹配
                time_matches = (rl[4] is None or str(rl[4]) == dayofweeks) and \
                              (rl[5] is None or str(rl[5]) == starttime) and \
                              (rl[6] is None or str(rl[6]) == endtime)
                
                if str(rl[3]) in actIdSet and time_matches:
                    conZ3 = cat.true_expr()
                    cons = rl[2]
                    if cons != None and cons != True:
                        cons = str(rl[2]).split(',')
                        for c in cons:
                            c = cat.conditionToZ3(cat.getCondition(c, db))
                            conZ3 = And(conZ3, c)
                    sameAct = Or(sameAct, conZ3)
                    ruleSet.append(rl)
            if s.check(Not(sameAct)) == unsat:
                atFlag = 1
                for rs in ruleSet:
                    result.append(rs[1])
    if not result:
        result.append('0')
    return atFlag, result

def alwaysTrue():
    res = []
    appletsList = list(cat.getAllRules())[:20]
    length = len(appletsList)
    triggerdic = {}
    actiondic = {}
    for i in range(length):
        num = appletsList[i][2]
        if num == None:
            triggerdic[i] = True
        else:
            exp = cat.conditionToZ3(cat.getCondition(num))
            triggerdic[i] = exp
        num = appletsList[i][3]
        if num == None:
            actiondic[i] = True
        else:
            exp = cat.actionToZ3(cat.getAction(num))
            actiondic[i] = exp

def f(appletsList,triggerdic,actiondic):
    s = time.time()
    solver = cat.new_solver()
    res = []
    length = len(appletsList)
    checkedRule = []
    for i in range(length):
        if appletsList[i][0] not in checkedRule:
            sameAct = [appletsList[i][1]]
            checkedRule.append(appletsList[i][0])
            triggerSet = []
            triggers = [triggerdic[num] for num in appletsList[i][2].split(',')]
            iTrigger = reduce(And,triggers)
            triggerSet.append(iTrigger)
            actions = [actiondic[num] for num in appletsList[i][3].split(',')]
            iAction = reduce(And,actions)

            for j in range(length):
                if appletsList[j][0] not in checkedRule and j!= i:
                    actions = [actiondic[num] for num in appletsList[j][3].split(',')]
                    jAction = reduce(And,actions)
                    if jAction == iAction:
                        sameAct.append(appletsList[j][1])
                        checkedRule.append(appletsList[j][0])
                        triggers = [triggerdic[num] for num in appletsList[j][2].split(',')]
                        jTrigger = reduce(And,triggers)
                        triggerSet.append(jTrigger)
            allset = reduce(Or,triggerSet)
            if solver.check(Not(allset)) == unsat:
                # res = res if res != [] else [1]
                res.append((sameAct))
    # print(time.time()-s)
    # print(res)
    # res = [0] if res == [] else res
    return res


