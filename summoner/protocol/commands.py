

# JSON? for agent automaton
class TRIGGER:
    always = 0
    on_stay = 1

    on_arrival = 100
    on_leave = 101
    on_abandon = 102
    
    on_exploration = 200

    on_backtrack = 300

# JSON?
class STATUS:
    uncomplete = 0 # failed
    stay = lambda t: {"status":1, "time": int(t)} # might also need to give a time JSON?

    complete = 100 # success
    leave = 101 # a kind of leaving
    abandon = 102 # a kind of leaving

    explore = 200 # keep automation state but triggers next nodes

    backtrack = 300 # go backward (cancel the state / but different memory)
    
    quite = 400 # stop running automaton


if __name__ == "__main__":
    print(TRIGGER.on_leave)
    print(STATUS.stay(34))

