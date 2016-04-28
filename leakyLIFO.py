
class LeakyLIFO(object):
    #Simple, leaky LIFO queue.  Pushing when LIFO is full simply pushes the
    #oldest element out of the queue.  Popping when empty returns None.
    #can get the full queue as a list, and can 'peek' at any index to get
    #its value

    def __init__(self, max_size=0):
        self._max_size = max_size
        self._stack = []

    def push(self, value):
        if len(self._stack) >= self._max_size:
            del self._stack[0]
        self._stack.append(value)

    def pop(self):
        if (len(self._stack) > 0):
            return self._stack.pop()
        else:
            return None

    def peek(self, index):
        return self._stack[index]

    def asList(self):
        return self._stack

    def size(self):
        return len(self._stack)
