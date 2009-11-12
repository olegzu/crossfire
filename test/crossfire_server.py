#!/usr/bin/env python
#
# crossfire_server.py
# Crossfire protocol server test implementation in python.
#
# M. G. Collins
#
# Copyright (c) 2009. All rights reserved.
#
# See license.txt for terms of usage.
#
# Usage:
# $> python crossfire_server.py [<host>] <port>
#

import json, socket, sys, threading

current_seq = 0

HANDSHAKE_STRING = "Fbug+CrossfireHandshake\r\n"

class CrossfireServer:

  def __init__(self, host, port):
    self.host = host
    self.port = port

  def start(self):
    self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
      self.socket.bind((self.host, self.port))
    except socket.error as err:
      print err
      sys.exit(1)

    self.socket.listen(1)
    self.conn, addr = self.socket.accept()

    self.socketCondition = threading.Condition()
    self.reader = PacketReader(self.conn, self.socketCondition)
    self.writer = PacketWriter(self.conn, self.socketCondition)

    self.waitHandshake()

  def stop(self):
    try:
      self.conn.close()
      self.socket.close()
      self.reader.join(0)
      self.writer.join(0)
    except AttributeError:
      pass

  def restart(self):
    self.stop()
    self.start()

  def waitHandshake(self):
    print 'Waiting for Crossfire handshake...'
    try:
      shake = self.conn.recv(25)
    except socket.error as err:
      print err
    if shake == HANDSHAKE_STRING:
      print 'Received Crossfire handshake.'
      self.conn.settimeout(1)
      self.conn.send(HANDSHAKE_STRING)
      
      self.socketCondition.acquire()
      self.writer.start()
      self.reader.start()
      self.socketCondition.notifyAll()
      self.socketCondition.release()

  def sendPacket(self, packet):
    self.writer.addPacket(packet)

  def getPacket(self):
    if self.reader:
      return self.reader.getPacket()


class PacketReader(threading.Thread):
  def __init__(self, conn, cv):
    threading.Thread.__init__(self)
    self.daemon = True
    self.packetQueue = []
    self.conn = conn
    self.cv = cv

  def getPacket(self):
    if len(self.packetQueue) > 0:
      return self.packetQueue.pop()

  def run(self):
    global current_seq

    while True:
      self.cv.acquire()
      length = self.readPacketLength()
      if length > 0:
        packet = self.readPacket(length)
        if packet:
          obj = json.loads(packet)
          current_seq = obj['seq'] +1
          self.packetQueue.append(obj)
      self.cv.notifyAll()
      self.cv.wait()

  def readPacketLength(self):
    length = 0
    buff = prev = curr = ""

    while prev != '\r' and curr != '\n':
      prev = curr
      try:
        curr = self.conn.recv(1)
        buff += str(curr)
      except socket.error:
        break

    cLen = buff.find("Content-Length:")

    if cLen > -1 and len(buff) > 16:
      length = int(buff[cLen+15:len(buff)-2])

    return length

  def readPacket(self, length):
    return self.conn.recv(length)


class PacketWriter(threading.Thread):
  def __init__(self, conn, cv):
    threading.Thread.__init__(self)
    self.daemon = True
    self.packetQueue = []
    self.conn = conn
    self.cv = cv

  def addPacket(self, packet):
    self.packetQueue.append(packet)

  def run(self):
    while True:
      self.cv.acquire()
      if len(self.packetQueue) > 0:
        packet = self.packetQueue.pop()
        json_str = json.dumps(packet.packet)
        packet_string = "Content-Length:" + str(len(json_str)) + "\r\n" + json_str
        self.conn.send(packet_string)

      self.cv.notifyAll()
      self.cv.wait()


class Command:
  def __init__(self, context_id, command_name, **arguments):
    global current_seq
    current_seq += 1
    self.seq = current_seq
    self.command = command_name
    self.packet = { "type": "request", "seq": self.seq, "context_id" : context_id, "command": command_name }
    if arguments:
      self.packet.update(arguments)


Commands = [
    "entercontext",
    "listcontexts",
    "version",
    "continue",
    "suspend",
    "evaluate",
    "backtrace",
    "frame",
    "scope",
    "scopes",
    "scripts",
    "source",
    "getbreakpoint",
    "getbreakpoints",
    "setbreakpoint",
    "changebreakpoint",
    "clearbreakpoint",
    "inspect",
    "lookup"
]

class CommandLine(threading.Thread):
  def __init__(self):
    threading.Thread.__init__(self)
    self.daemon = True
    self.commands = []
    self.current_context = ""

  def getCommand(self):
    if len(self.commands) > 0:
      return self.commands.pop()

  def run(self):
    while True:
      line = sys.stdin.readline()
      if line:
        line = line.strip()
        space = line.find(' ')
        argstr = None
        if space == -1:
          command = line
        else:
          command = line[:space].strip()
          argstr = line[space:].strip()
        if command in Commands:
          if command == "entercontext":
            self.current_context = argstr
            print "Entering context: " + self.current_context + "\n"
          else:
            args = {}
            if argstr:
              try:
                args = json.loads(argstr)
              except ValueError:
                print "Failed to parse arguments."
            self.commands.append(Command(self.current_context, command, arguments=args))
        elif command:
          print "Unknown command: " + command + "\n"

  def stop(self):
    self.join(0)

if __name__ == "__main__":

  def main():

    host = None
    port = None

    if len(sys.argv[1:]) == 1:
      host = socket.gethostname()
      port = sys.argv[1]
    if len(sys.argv[1:]) == 2:
      host = sys.argv[1]
      port = sys.argv[2]

    if host and port:
      print 'Starting Crossfire server on ' + host + ':' + port + " (Ctrl-C to stop)"
      server = CrossfireServer(host, int(port))
      commandLine = CommandLine()

      try:
        commandLine.start()
        server.start()
        
        print "Sending version command...\n"
        command = Command("", "version")
        server.sendPacket(command)

        print "Listing contexts...\n"
        command = Command("", "listcontexts")
        server.sendPacket(command)

        while True:
            packet = server.getPacket()
            if packet:
              json.dump(packet, sys.stdout, sort_keys=True, indent=2)
              print "\n"
              if 'event' in packet and packet['event'] == "closed":
                  server.restart()

            command = commandLine.getCommand()
            if command:
              print "Sending command => " + command.command + "\n"
              server.sendPacket(command)

      except KeyboardInterrupt:
        print "\nStopping server...\n"
        server.stop()
        commandLine.stop()
    else:
      print 'No host and/or port specified.'
      print 'Usage: $> python crossfire_server.py [<host>] <port>'

    sys.exit(0)


# kickstart
  main()