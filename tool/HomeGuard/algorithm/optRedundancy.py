# -*- coding:utf-8 -*- 
from z3 import *
import connectAndTransfer as cat
import time
from functools import reduce

# # 蕴含关系，指形如：a<25和a<20的指令

def redundancy():
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
            if solver.check(Not(Implies(iTrigger, jTrigger))) == unsat \
            and solver.check(Not(Implies(iAction,jAction))) == unsat:
                # res = res if res != [] else [1]
                res.append((appletsList[i][1] + ' ,and, ' + appletsList[j][1]))
                
            elif solver.check(Not(Implies(jTrigger, iTrigger))) == unsat \
            and solver.check(Not(Implies(jAction,iAction))) == unsat:
                # res = res if res != [] else [1]
                res.append((appletsList[i][1] + ' ,and, ' + appletsList[j][1]))
    return res
