// Dump the visible text of an app's focused window through the Accessibility API.
// Usage: axdump <max_nodes> <max_chars> <max_seconds> <unlock 0|1> [pid]
// Prints JSON lines {"role","text","depth"} in breadth-first order, then one {"meta":{...}} line.
import AppKit
import ApplicationServices
import Foundation

let args = CommandLine.arguments
func arg(_ i: Int, _ fallback: Double) -> Double { args.count > i ? Double(args[i]) ?? fallback : fallback }
let maxNodes = Int(arg(1, 2000)), maxChars = Int(arg(2, 8000)), budget = arg(3, 1.0), unlock = arg(4, 1) != 0
let started = Date()
var meta: [String: Any] = ["helper": "swift", "trusted": AXIsProcessTrusted()]

func emit(_ object: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: object), let line = String(data: data, encoding: .utf8) {
        print(line)
    }
}

func finish(_ status: String) -> Never {
    meta["status"] = status
    meta["ms"] = Int(Date().timeIntervalSince(started) * 1000)
    emit(["meta": meta])
    exit(0)
}

if !AXIsProcessTrusted() { finish("denied") }
var pid: pid_t
if args.count > 5, let given = Int32(args[5]) {
    pid = given
} else if let front = NSWorkspace.shared.frontmostApplication {
    pid = front.processIdentifier
} else {
    finish("no_app")
}
meta["pid"] = Int(pid)
meta["app"] = NSRunningApplication(processIdentifier: pid)?.localizedName ?? ""

let app = AXUIElementCreateApplication(pid)
AXUIElementSetMessagingTimeout(app, Float(min(0.25, budget)))
if unlock {
    meta["unlock"] = AXUIElementSetAttributeValue(app, "AXManualAccessibility" as CFString, kCFBooleanTrue).rawValue
}
var window: CFTypeRef?
let windowErr = AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute as CFString, &window)
if windowErr == .apiDisabled { finish("denied") }
guard windowErr == .success, let root = window else { finish("no_window") }

let attributes = [kAXRoleAttribute, kAXSubroleAttribute, kAXValueAttribute, kAXTitleAttribute,
                  kAXDescriptionAttribute, kAXChildrenAttribute] as CFArray
func string(_ value: AnyObject) -> String? {
    guard let text = value as? String else { return nil }
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.isEmpty ? nil : trimmed
}
var queue: [(AXUIElement, Int)] = [(root as! AXUIElement, 0)]
var head = 0, nodes = 0, chars = 0, seen = Set<String>(), stop = ""
while head < queue.count {
    if nodes >= maxNodes { stop = "nodes"; break }
    if Date().timeIntervalSince(started) > budget { stop = "time"; break }
    let (element, depth) = queue[head]
    head += 1
    nodes += 1
    var values: CFArray?
    guard AXUIElementCopyMultipleAttributeValues(element, attributes, [], &values) == .success,
          let list = values as? [AnyObject], list.count == 6 else { continue }
    let role = list[0] as? String ?? ""
    if role == "AXSecureTextField" || list[1] as? String == "AXSecureTextField" { continue }
    if let full = string(list[2]) ?? string(list[3]) ?? string(list[4]), seen.insert(full).inserted {
        let text = String(full.prefix(maxChars - chars))
        chars += text.count
        emit(["role": role, "text": text, "depth": depth])
        if chars >= maxChars { stop = "chars"; break }
    }
    if let children = list[5] as? [AXUIElement] {
        queue.append(contentsOf: children.map { ($0, depth + 1) })
    }
}
meta["nodes"] = nodes
meta["chars"] = chars
meta["stop"] = stop
finish("ok")
