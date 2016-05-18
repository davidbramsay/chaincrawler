import sys
import logging


level = logging.WARN

#set log output to screen and level to DEBUG level
logging.basicConfig(stream=sys.stderr)

#create one shared instance of logging
log = logging.getLogger(__name__)
log.setLevel(level)
log.propagate = 0

ch = logging.StreamHandler()
ch.setLevel(level)
log.addHandler(ch)
