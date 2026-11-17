// oxpecker-expect[1.9.2]: equal
// oxpecker-expect[master]: equal
// oxpecker-version-change[1.9.2->master]: A regexp instance has only `lastIndex` as an own property on master; 1.9.2 also had own `unicode`, `sticky`, ... Fixed on master: the flags are accessors on RegExp.prototype (spec 22.2.6).
// Found by the cross-version loop, 2026-11-11 (oxpecker fuzz --against 1.9.2).
var o3 = /a/;
__probe("step", o3);
