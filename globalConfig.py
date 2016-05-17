import sys
import logging


#set log output to screen and level to DEBUG level
logging.basicConfig(stream=sys.stderr, level=logging.WARN)

#create one shared instance of logging
log = logging.getLogger()

