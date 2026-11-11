// oxpecker-expect[1.9.2]: hard
// oxpecker-expect[master]: hard
// oxpecker-version-change[1.9.2->master]: Interpreted: resuming a continuation captured in a `let` loop sees the iteration's own binding on master ([0,1]); 1.9.2 gave [0,0]. Probably fixed on master (per-iteration bindings, spec CreatePerIterationEnvironment; not checked in the source). Compiled refuses continuations on both.
// Found by the cross-version loop, 2026-11-11 (oxpecker fuzz --against 1.9.2).
var arr9 = [];
for (let i10 = 0; i10 < 2; i10++) {
    const v13 = __pause(i10);
    arr9.push(v13);
}
var n16 = arr9.length;
__probe("step", n16, arr9);
