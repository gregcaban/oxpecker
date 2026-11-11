// [array]
var arr0 = [];
// [string]
const s1 = "􋉒򹞜µ¹z";
// [integer]
let n2 = 0x6708;
__probe("step", n2, s1, arr0);
// [string]
const s3 = "Ó򚰣";
__probe("step", s3, n2, s1, arr0);
// [for_in_loop]
for (let i4 in arr0) {
    const n6 = 1000.0;
    -n6;
    arr0[0] = s1;
}
__probe("step", s3, n2, s1, arr0);
function f8(p9) {
    let arr10 = [arr0, n2];
    p9 = n2;
    p9 = n2;
    var n12 = 0;
    var n13 = n12;
    return p9;
}
for (let i14 = 0; i14 < 10; i14++) {
    if (i14 == 5) {
        delete arr0.m;
        arr0[arr0];
    }
    f8(n2);
}
__probe("end", f8, s3, n2, s1, arr0);
