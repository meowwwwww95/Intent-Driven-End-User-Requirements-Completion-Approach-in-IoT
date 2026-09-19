import connectAndTransfer as cat
from z3 import *
from functools import reduce
import time

def actCon():
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
    aSolver = cat.new_solver()
    tSolver = cat.new_solver()
    res = []
    length = len(appletsList)
    for i in range(length):
        triggers = [triggerdic[num] for num in appletsList[i][2].split(',')]
        iTrigger = reduce(And,triggers)
        actions = [actiondic[num] for num in appletsList[i][3].split(',')]
        iAction = reduce(And,actions)
        for j in range(i+1,length):
            triggers = [triggerdic[num] for num in appletsList[j][2].split(',')]
            jTrigger = reduce(And,triggers)
            actions = [actiondic[num] for num in appletsList[j][3].split(',')]
            jAction = reduce(And,actions)
            
            tSolver.push()
            tSolver.add(iTrigger)
            tSolver.add(jTrigger)
            aSolver.push()
            aSolver.add(iAction)
            aSolver.add(jAction)

            t_res = tSolver.check()
            a_res = aSolver.check()
            # print(f"DEBUG: Comparing {appletsList[i][0]} and {appletsList[j][0]}: Trigger={t_res}, Action={a_res}")
            
            if t_res == sat and a_res == unsat :
                # res = res if res != [] else [1]
                res.append((appletsList[i][1] + ' ,and, ' + appletsList[j][1]))
            tSolver.pop()
            aSolver.pop()
    # print(time.time()-s)
    # print(res)
    # res = [0] if res == [] else res
    return res
    
