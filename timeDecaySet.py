from datetime import datetime
import time


class TimeDecaySet(object):
    #a simple set that you initiate with a minutes value,
    #that will only keep values that are younger than X minutes old.
    #after X minutes, they will be removed from the set.

    #set minute_decay = 0 for infinite persistence

    def __init__(self, minute_decay=1):
        self._minute_decay = minute_decay
        self._list = []


    def add(self, value):
        #only add if not in set
        if self.in_set(value):
            return False
        else:
            #push value with unix timestamp
            self._list.append({'val':value, \
                    'timestamp':time.mktime(datetime.now().timetuple())})
            return True


    def in_set(self, value):
        self.remove_timed_out_values()
        if (value in (x['val'] for x in self._list)):
            return True
        else:
            return False


    def remove_from_set(self, value):
        self._list = [x for x in self._list if not x['val']==value]


    def remove_timed_out_values(self):
        #remove all expired values - internal function

        #since they are appended chronologically, we can simply find
        #the index where now-time>minutes and remove everything before that
        index = 0
        now = time.mktime(datetime.now().timetuple())

        while (index<len(self._list) and ((now - self._list[index]['timestamp'])/60 > self._minute_decay)):
            index = index + 1

        #only remove values if our minute_decay value has been set to a positive value
        if (self._minute_decay > 0):
            self._list = self._list[index:]


    def asList(self):
        self.remove_timed_out_values()
        return [x['val'] for x in self._list]


    def size(self):
        self.remove_timed_out_values()
        return len(self._list)
