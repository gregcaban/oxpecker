// Adapted from test262 test/language/statements/try/completion-values-fn-finally-abrupt.js
// (BSD licence, Copyright (C) 2020 Salesforce.com). Rhino's test262.properties marks it as
// failing in compiled mode only. The Test262Error/assert harness is replaced with probes.
var fn, count = {};
var log = [];
function Test262Error(m) { this.message = m; }
function attempt(label, f) {
  try { log.push(label + ":" + f()); } catch (e) { log.push(label + ":threw " + (e instanceof Test262Error ? "T262" : e)); }
}

// 1: try Abrupt, catch Abrupt, finally Abrupt; Completion: finally
count.catch = 0; count.finally = 0;
fn = function() {
  try { throw 'try'; }
  catch (e) { count.catch += 1; throw 'catch'; }
  finally { count.finally += 1; throw new Test262Error('finally'); }
  return 'wat';
};
attempt("1", fn);
__probe("1", count.catch, count.finally);

// 2: try Abrupt, catch Return, finally Abrupt; Completion: finally
count.catch = 0; count.finally = 0;
fn = function() {
  try { throw 'try'; }
  catch (e) { count.catch += 1; return 'catch'; }
  finally { count.finally += 1; throw new Test262Error('finally'); }
  return 'wat';
};
attempt("2", fn);
__probe("2", count.catch, count.finally);

// 3: try Return, catch Return, finally Abrupt; Completion: finally
count.catch = 0; count.finally = 0;
fn = function() {
  try { return 'try'; }
  catch (e) { count.catch += 1; return 'catch'; }
  finally { count.finally += 1; throw new Test262Error('finally'); }
  return 'wat';
};
attempt("3", fn);
__probe("3", count.catch, count.finally);
log;
