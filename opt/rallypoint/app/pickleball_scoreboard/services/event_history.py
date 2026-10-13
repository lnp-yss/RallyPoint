from collections import deque
from copy import deepcopy


class EventHistory:
    def __init__(self, limit=100):
        self.states = deque(maxlen=limit)

    def push(self, state):
        self.states.append(deepcopy(state))

    def pop(self):
        return self.states.pop() if self.states else None

    def __len__(self):
        return len(self.states)
