static int helper_value(void) {
    return 7;
}

int sample_target(int x) {
    int current = helper_value();
    return x + current;
}
