// [null]
const v0 = null;
// [string]
const s1 = "\ud800";
// [bigint]
const big2 = 8500n;
// [string]
var s3 = "";
// [while_loop]
var n5 = 0;
while ((() => {
    let b7 = n5 < 1;
    return b7;
})()) {
    var o9 = {};
    var o10 = {};
    var o11 = {};
    var o12 = {};
    o9.foo = big2;
    o10.foo = s1;
    o10.d = o12;
    o11.foo = s1;
    o11.d = o9;
    o11.n = o11;
    o12.foo = o10;
    o12.d = s1;
    o12.x = o12;
    const f13 = isNaN;
    const n14 = n5++;
}
__probe("step", n5, s3, big2, s1, v0);
// [update]
n5 ||= n5;
__probe("step", n5, s3, big2, s1, v0);
// [binary]
var n15 = 0 - v0;
__probe("step", n15, n5, s3, big2, s1, v0);
