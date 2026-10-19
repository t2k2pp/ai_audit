import 'package:flutter/material.dart';
import 'util.dart';

/// Counter
class Counter extends Base with Mix implements Iface {
  int value = 0;
  Counter(this.value);
  void inc() {
    value++;
    log('x');
  }
  int get doubled => value * 2;
}

int add(int a, int b) {
  return helper(a) + b;
}
