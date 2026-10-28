// Rhino b95f75d: in compiled mode a relational comparison (<, <=, >, >=) between a function
// local or parameter and a number literal throws TypeError "Cannot convert BigInt to a number"
// when the variable holds a BigInt; the interpreter compares (1024n < 5 is false). Either operand
// order and a double literal (5.5) are affected. The literal's side can also be a local the
// optimizer types as a number (`const n = 5; v < n`), and the BigInt side can be a parameter, a
// local, or a captured top-level variable (`captured`). Agrees: ==, ===, a string literal ("5"),
// an untyped operand (a parameter `five`), and the same comparison at the top level. A try/catch
// inside the function also makes the modes agree (it forces an activation frame, so the variable
// is no longer a direct local), which is why the catch sits at the call site here.
// Not compiled-bigint-in-number-typed-local.js: no numeric operator touches the variable, a bare
// parameter compared with a literal is enough.
//   interpreted: the comparison's value ("false"/"true")   compiled: "TypeError" for lt, gt, leftLiteral, dbl, local, constLocal, captured
// Found by `oxpecker fuzz` (hour-long campaign, jit_tricky_function: `v10 = big1; return v10 < 5`
// in an arrow used as a do-while condition), 2026-10-27; the `const n` and captured forms came
// out of the 2026-10-28 campaign under the same coarse signature.
// Replay: oxpecker diff tests/regressions/compiled-bigint-relop-number-literal-local.js
function lt(v) { return v < 5; }
function gt(v) { return v > 5; }
function leftLiteral(v) { return 5 <= v; }
function dbl(v) { return v >= 5.5; }
function local() { var b = 1024n; return b < 5; }
function constLocal(v) { const n = 5; return v < n; }
var top = 1024n;
function captured() { return top < 5; }
function eq(v) { return v == 5; }
function noLiteral(v, five) { return v < five; }
function outcome(f, a, b) { try { return String(f(a, b)); } catch (e) { return e.name; } }
__probe("step", outcome(lt, 1024n), outcome(gt, 1024n), outcome(leftLiteral, 1024n), outcome(dbl, 1024n), outcome(local),
        outcome(constLocal, 1024n), outcome(captured), outcome(eq, 1024n), outcome(noLiteral, 1024n, 5), String(top < 5));
