# -*- coding:utf-8 -*- 
from z3 import *
import connectAndTransfer as cat
import time
import ast
from functools import reduce

#单独校验的时候用此函数，全部校验统一用f()
def policyCon():
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
    #建立链接表
    solver.push()
    effects = cat.getEffect(db)
    for e in effects:
        solver.append(e)
    # print(solver)
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
            # print(Implies(jTrigger,iAction))
            # print(Implies(iTrigger,jAction))
            if solver.check((Implies(jTrigger,iAction))) == sat :
                linkTable[i][j] = True
            elif solver.check((Implies(iTrigger,jAction))) == sat :
                linkTable[j][i] = True
    for i in range(length):
        for j in range(length):
            for k in range(length):
                if i != j and linkTable[i][k] == True and linkTable[k][j] == True:
                    linkTable[i][j] = True
    solver.pop()
    policy = cat.getPolicy(db)
    # 检验
    return f(appletsList,triggerdic,actiondic,linkTable,policy)

def f(appletsList,triggerdic,actiondic,linkTable,policy):
    s = time.time()
    solver = cat.new_solver()
    pSolver = cat.new_solver()
    for p in policy:
        pSolver.append(p)
    # solver.set(unsat_core=True)
    # solver.assert_and_track(policy,'p')
    # print(pSolver,appletsList,linkTable)
    res = []
    length = len(appletsList)
    for i in range(length):

        triggers = [triggerdic[num] for num in appletsList[i][2].split(',')]
        iTrigger = reduce(And,triggers)
        actions = [actiondic[num] for num in appletsList[i][3].split(',')]
        iAction = reduce(And,actions)
        if solver.check(And(iTrigger,iAction)) == sat \
            and pSolver.check(And(iTrigger,iAction)) == unsat :
            # res = res if res != [] else [1]
            res.append((appletsList[i][1]))
            continue

        for j in range(length):
            if i == j : continue
            triggers = [triggerdic[num] for num in appletsList[j][2].split(',')]
            jTrigger = reduce(And,triggers)
            actions = [actiondic[num] for num in appletsList[j][3].split(',')]
            jAction = reduce(And,actions)
            if (solver.check(And(iTrigger,jTrigger)) == sat or linkTable[i][j]) and \
                solver.check(And(iAction,jAction)) == sat and \
                    pSolver.check(And(iAction,jAction)) == unsat :
                # res = res if res != [] else [1]
                res.append((appletsList[i][1] + ' ,and, ' + appletsList[j][1]))

            if linkTable[i][j] == False: continue
            if solver.check(And(iTrigger,jAction)) == sat and \
                pSolver.check(And(iTrigger,jAction)) == unsat:
                # res = res if res != [] else [1]
                res.append((appletsList[i][1] + ' ,and, ' + appletsList[j][1]))
 
    return res


