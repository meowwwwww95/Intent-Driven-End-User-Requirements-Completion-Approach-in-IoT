import connectAndTransfer as cat
from z3 import *
from functools import reduce
import time

def traverseTable(userId=''):
    res = []
    rule = cat.getAllRules(userId=userId)
    for r in rule:
        c = r[2]
        act = r[3]
        if c != None and act != None:
            cs = c.split(',')
            acts = act.split(',')
            for c in cs:
                for act in acts:
                    conZ3 = cat.conditionToZ3(cat.getCondition(c))
                    actZ3 = cat.actionToZ3(cat.getAction(act))
                    solver = cat.new_solver()
                    solver.add(And(conZ3,actZ3))
                    if solver.check() == unsat:
                        res.append(r[0])
                        break
    return res


def selfCon():
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
    # appletsList = [('1', 'a', '127', '151', '1,2,3,4,5,6,7', '00:00:00', '23:59:59', '5', '1')]
    # print(appletsList)
    s = time.time()
    solver = cat.new_solver()
    res = []
    length = len(appletsList)
    for i in range(length):
        triggers = [triggerdic[num] for num in appletsList[i][2].split(',')]
        iTrigger = reduce(And,triggers)
        actions = [actiondic[num] for num in appletsList[i][3].split(',')]
        iAction = reduce(And,actions)
        solver.push()
        solver.append(iTrigger)
        solver.append(iAction)
        if solver.check() == unsat:
            # res = res if res != [] else [1]
            res.append((appletsList[i][1]))
        solver.pop()
    # print(time.time()-s)
    # print(res)
    # res = [0] if res == [] else res
    return res
    
