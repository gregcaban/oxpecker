// engine262 as a by-the-spec oracle for oxpecker findings (oxpecker/judge.py): runs one case the
// way the Java harness runs it in one Rhino mode (harness-java Host.java, ModeRunner.java) and
// prints one outcome in the harness's JSON shape, mode "engine262".
//
// The harness's own canon.js and prelude.js run unchanged inside engine262, so values are
// rendered by the same code: __probe(label, ...) and print() go through the canonicaliser, the
// completion value and a thrown value too. There is no Java here: J, Java types and
// continuations (__pause) are refused by judge.py before a case gets here.
//
// Usage: node judge/host.mjs < {"source": ..., "strict": false, "seed": 1}
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  Agent, setSurroundingAgent, surroundingAgent, ManagedRealm, CreateBuiltinFunction, CreateDataProperty,
  ThrowCompletion, NormalCompletion, Value, ParseScript, isProxyExoticObject, EnsureCompletion, X,
} from '@engine262/engine262';

const RESOURCES = fileURLToPath(new URL('../harness-java/src/main/resources/', import.meta.url));
const CANON_SRC = readFileSync(RESOURCES + 'canon.js', 'utf8');
const PRELUDE_SRC = readFileSync(RESOURCES + 'prelude.js', 'utf8');

const req = JSON.parse(readFileSync(0, 'utf8'));
const out = { mode: 'engine262', status: 'ok', value: null, valueJavaTypes: null, thrown: null,
  trace: [], traceJavaTypes: [], output: [], soft: [], continuations: [], javaTrace: [], javaTraceTypes: [] };

const agent = new Agent({});
setSurroundingAgent(agent);
const realm = new ManagedRealm({});
const str = (v) => (v && typeof v.stringValue === 'function' ? v.stringValue() : String(v));

let last = Value.undefined;   // the value the host hands to the in-realm canonicaliser
let canonResult = null;
const native = {
  // __oxRecord(kind, text, javaTypes, soft[, name])
  __oxRecord(args) {
    const [kind, text, jt, soft, name] = args.map(str);
    if (soft && soft !== 'undefined' && soft !== '') out.soft.push(soft);
    if (kind === 'soft-only') return Value.undefined;
    if (kind === 'trace') { out.trace.push(text); out.traceJavaTypes.push(jt); }
    else if (kind === 'output') out.output.push(text);
    else canonResult = { text, jt, name };
    return Value.undefined;
  },
  __oxIsProxy(args) { return args.length && isProxyExoticObject(args[0]) ? Value.true : Value.false; },
  __oxLast() { return last; },
};

function evaluate(src, name) {
  return EnsureCompletion(realm.evaluateScriptSkipDebugger(src, { specifier: name }));
}

function setup() {
  const pop = realm.pushTopContext();
  for (const [name, fn] of Object.entries(native)) {
    const f = CreateBuiltinFunction((args) => fn(args), 1, Value(name), []);
    X(CreateDataProperty(realm.GlobalObject, Value(name), f));
  }
  pop?.();
  // As Host.install: print, __probe, the canonicaliser over the helpers, then the prelude.
  const boot = `(function () {
    var rec = __oxRecord, isProxy = __oxIsProxy, String_ = String;
    var api = (${CANON_SRC})({ jtype: function () { return 'none'; }, isProxy: isProxy,
                               javaClass: function () { return '?'; }, global: globalThis });
    var canon = api.canon, errName = api.errName;
    globalThis.__oxCanonLast = function (withName) {
      var v = __oxLast(), k = canon(v);
      rec('value', k[0], k[1], k[2], withName ? errName(v) : '');
    };
    globalThis.print = function print() {
      var s = '';
      for (var i = 0; i < arguments.length; i++) s += (i ? ' ' : '') + String_(arguments[i]);
      rec('output', s, '', '');
    };
    globalThis.__probe = function __probe() {
      var t = [], j = [];
      for (var i = 0; i < arguments.length; i++) {
        var k = canon(arguments[i]);
        t.push(k[0]); j.push(k[1]);
        if (k[2]) rec('soft-only', '', '', k[2]);
      }
      rec('trace', t.join(' | '), j.join(' | '), '');
    };
    globalThis.__isProxy = function (v) { return isProxy(v); };
    globalThis.__jtype = function () { return 'none'; };
    (${PRELUDE_SRC}).call(globalThis, ${Number(req.seed ?? 1)});
  })();`;
  const c = evaluate(boot, 'oxpecker-boot.js');
  if (c instanceof ThrowCompletion) throw new Error('setup: ' + describe(c.Value));
}

function canonOf(value, withName) {
  last = value;
  canonResult = null;
  const c = evaluate('__oxCanonLast(' + (withName ? 'true' : 'false') + ')', 'oxpecker-canon.js');
  if (c instanceof ThrowCompletion || !canonResult) return { text: '!canon', jt: '', name: '?' };
  return canonResult;
}

function describe(v) {
  try { return canonOf(v, true).text; } catch (e) { return String(e); }
}

function drainJobs() {
  try { surroundingAgent.eventLoop?.run?.('manual'); } catch (e) { /* no event loop in this build */ }
}

try {
  setup();
  const src = (req.strict ? '"use strict";\n' : '') + req.source;
  const parsed = (() => { const pop = realm.pushTopContext(); try { return ParseScript(src, realm, { specifier: req.sourceName || 'case.js' }); } finally { pop?.(); } })();
  if (Array.isArray(parsed)) {
    out.status = 'syntax';
    const k = canonOf(parsed[0], true);
    out.thrown = { kind: 'syntax', name: k.name || 'SyntaxError', message: null, value: null, valueJavaTypes: null };
  } else {
    const c = EnsureCompletion(realm.evaluateScriptSkipDebugger(parsed));
    drainJobs();
    if (c instanceof ThrowCompletion) {
      out.status = 'throw';
      const k = canonOf(c.Value, true);
      out.thrown = { kind: 'js-value', name: k.name, value: k.text, valueJavaTypes: k.jt, message: null };
    } else {
      const k = canonOf(c instanceof NormalCompletion ? c.Value : c, false);
      out.value = k.text;
      out.valueJavaTypes = k.jt;
    }
  }
} catch (e) {
  out.status = 'crash';
  out.engineException = { clazz: e && e.name, message: String(e && e.message), stack: String(e && e.stack).split('\n').slice(0, 8) };
}
process.stdout.write(JSON.stringify(out) + '\n');
