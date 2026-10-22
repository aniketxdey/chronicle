// JXA fallback for axdump.swift with the same arguments and output.
// Usage: osascript -l JavaScript axdump.js <max_nodes> <max_chars> <max_seconds> <unlock 0|1> [pid]
ObjC.import('AppKit');
ObjC.import('ApplicationServices');

function run(argv) {
  const started = Date.now();
  const maxNodes = parseInt(argv[0] || '2000', 10);
  const maxChars = parseInt(argv[1] || '8000', 10);
  const budget = parseFloat(argv[2] || '1');
  const unlock = (argv[3] || '1') !== '0';
  const lines = [];
  const meta = {helper: 'jxa', trusted: $.AXIsProcessTrusted()};
  const finish = status => {
    meta.status = status;
    meta.ms = Date.now() - started;
    lines.push(JSON.stringify({meta: meta}));
    return lines.join('\n');
  };
  if (!meta.trusted) return finish('denied');
  let pid = parseInt(argv[4] || '', 10);
  if (isNaN(pid)) {
    const front = $.NSWorkspace.sharedWorkspace.frontmostApplication;
    if (!front || front.isNil()) return finish('no_app');
    pid = front.processIdentifier;
  }
  const running = $.NSRunningApplication.runningApplicationWithProcessIdentifier(pid);
  meta.pid = pid;
  meta.app = running.isNil() ? '' : ObjC.unwrap(running.localizedName);

  const app = $.AXUIElementCreateApplication(pid);
  $.AXUIElementSetMessagingTimeout(app, Math.min(0.25, budget));
  if (unlock) meta.unlock = $.AXUIElementSetAttributeValue(app, $('AXManualAccessibility'), $.NSNumber.numberWithBool(true));
  const ref = Ref('^@');
  const err = $.AXUIElementCopyAttributeValue(app, $('AXFocusedWindow'), ref);
  if (err === -25211) return finish('denied');
  if (err !== 0) return finish('no_window');

  // AX calls need the ObjC object form; raw Refs are rejected by the bridge.
  const queue = [[ObjC.castRefToObject(ref[0]), 0]];
  const attrs = $(['AXRole', 'AXSubrole', 'AXValue', 'AXTitle', 'AXDescription', 'AXChildren']);
  const seen = {};
  let head = 0, nodes = 0, chars = 0, stop = '';
  while (head < queue.length) {
    if (nodes >= maxNodes) { stop = 'nodes'; break; }
    if ((Date.now() - started) / 1000 > budget) { stop = 'time'; break; }
    const [element, depth] = queue[head++];
    nodes++;
    const out = Ref('^@');
    if ($.AXUIElementCopyMultipleAttributeValues(element, attrs, 0, out) !== 0) continue;
    const values = ObjC.castRefToObject(out[0]);
    if (Number(values.count) !== 6) continue;
    const str = i => {
      const value = values.objectAtIndex(i);
      return value.isKindOfClass($.NSString) ? ObjC.unwrap(value).trim() : '';
    };
    const role = str(0);
    if (role === 'AXSecureTextField' || str(1) === 'AXSecureTextField') continue;
    let text = str(2) || str(3) || str(4);
    if (text && !Object.prototype.hasOwnProperty.call(seen, text)) {
      seen[text] = true;
      text = text.slice(0, maxChars - chars);
      chars += text.length;
      lines.push(JSON.stringify({role: role, text: text, depth: depth}));
      if (chars >= maxChars) { stop = 'chars'; break; }
    }
    const children = values.objectAtIndex(5);
    if (children.isKindOfClass($.NSArray)) {
      for (let i = 0, n = Number(children.count); i < n; i++) queue.push([children.objectAtIndex(i), depth + 1]);
    }
  }
  meta.nodes = nodes;
  meta.chars = chars;
  meta.stop = stop;
  return finish('ok');
}
