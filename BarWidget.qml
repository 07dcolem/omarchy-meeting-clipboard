import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Calendar glyph. Left click reads the clipboard once and opens the meeting.
// The helper prints no meeting id, passcode, or URL, and this widget does
// not keep what the helper writes.
BarWidget {
  id: root
  moduleName: "io.github.07dcolem.meeting-clipboard"

  readonly property string helper: {
    var url = Qt.resolvedUrl("teams-join-from-clipboard.py").toString()
    if (url.indexOf("file://") === 0)
      url = url.substring(7)
    try {
      return decodeURIComponent(url)
    } catch (e) {
      return url
    }
  }
  // nf-md-calendar, the glyph the clock draws beside the date.
  readonly property string calendarGlyph: "󰃭"
  readonly property int outputCap: 2048

  property bool busy: false
  property bool overflow: false
  property bool timedOut: false
  property bool stuckNoted: false
  property int stdoutUnits: 0
  property int stderrUnits: 0
  property int generation: 0

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function join() {
    if (root.busy || root.helper === "")
      return
    root.generation += 1
    root.busy = true
    root.overflow = false
    root.timedOut = false
    root.stuckNoted = false
    root.stdoutUnits = 0
    root.stderrUnits = 0
    joinProc.running = false
    joinProc.command = ["/usr/bin/python3", "-I", "-S", root.helper, "--plugin"]
    deadline.restart()
    joinProc.running = true
  }

  function noteStuck() {
    if (root.stuckNoted)
      return
    root.stuckNoted = true
    noteProc.running = false
    noteProc.command = [
      "/usr/bin/notify-send",
      "-a", "meeting-clipboard",
      "-u", "normal",
      "Meeting clipboard",
      "The join helper did not finish."
    ]
    noteDeadline.restart()
    noteProc.running = true
  }

  function finish(token) {
    if (token !== root.generation)
      return
    var stuck = root.overflow || root.timedOut
    deadline.stop()
    killTimer.stop()
    root.busy = false
    root.stdoutUnits = 0
    root.stderrUnits = 0
    if (stuck)
      root.noteStuck()
  }

  Timer {
    id: deadline
    interval: 12000
    repeat: false
    onTriggered: {
      root.timedOut = true
      if (joinProc.running)
        joinProc.signal(15)
      killTimer.restart()
    }
  }

  Timer {
    id: killTimer
    interval: 2000
    repeat: false
    onTriggered: {
      if (joinProc.running)
        joinProc.signal(9)
    }
  }

  Process {
    id: joinProc
    stdout: SplitParser {
      splitMarker: ""
      onRead: function(chunk) {
        root.stdoutUnits += chunk.length
        if (root.stdoutUnits > root.outputCap) {
          root.overflow = true
          root.stdoutUnits = 0
          if (joinProc.running)
            joinProc.signal(15)
        }
      }
    }
    stderr: SplitParser {
      splitMarker: ""
      onRead: function(chunk) {
        root.stderrUnits += chunk.length
        if (root.stderrUnits > root.outputCap) {
          root.overflow = true
          root.stderrUnits = 0
          if (joinProc.running)
            joinProc.signal(15)
        }
      }
    }
    onExited: function(_code, _status) {
      var token = root.generation
      Qt.callLater(function() { root.finish(token) })
    }
  }

  Timer {
    id: noteDeadline
    interval: 3000
    repeat: false
    onTriggered: {
      if (noteProc.running)
        noteProc.signal(15)
    }
  }

  Process {
    id: noteProc
    stdout: SplitParser {
      splitMarker: ""
      onRead: function(chunk) {
        if (chunk.length > 0 && noteProc.running)
          noteProc.signal(15)
      }
    }
    onExited: function(_code, _status) {
      noteDeadline.stop()
    }
  }

  Component.onDestruction: {
    deadline.stop()
    killTimer.stop()
    if (joinProc.running)
      joinProc.signal(15)
    if (noteProc.running)
      noteProc.signal(15)
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    slotSize: Style.bar.iconSlot
    text: root.calendarGlyph
    active: root.busy
    pressable: !root.busy
    tooltipText: "Join the meeting on the clipboard"

    onPressed: function(mouseButton) {
      if (mouseButton === Qt.LeftButton)
        root.join()
    }
  }
}
