// oxpecker-expect[1.9.2]: equal
// oxpecker-expect[master]: equal
// oxpecker-version-change[1.9.2->master]: `arr.length = undefined` throws RangeError on master, TypeError on 1.9.2. Fixed on master: ArraySetLength throws RangeError when ToNumber(v) is not ToUint32(v).
// Found by the cross-version loop, 2026-11-11 (oxpecker fuzz --against 1.9.2).
var arr0 = [];
var o6 = {
    __proto__: arr0,
    m() {
    },
};
var v8 = o6["m"]();
o6[arr0] >>= v8;
arr0.length = v8;
