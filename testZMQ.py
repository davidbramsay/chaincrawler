import zmq

context = zmq.Context()
testReceive = context.socket(zmq.PULL)
testReceive.connect("tcp://127.0.0.1:5557")

while True:
    uri = testReceive.recv_string()
    print "+++++++++++++++++++++++++"
    print "+++++++++++++++++++++++++"
    print "+++++++++++++++++++++++++"
    print "+++++++++++++++++++++++++"
    print uri
    print "+++++++++++++++++++++++++"
    print "+++++++++++++++++++++++++"
    print "+++++++++++++++++++++++++"
    print "+++++++++++++++++++++++++"
    print "+++++++++++++++++++++++++"
