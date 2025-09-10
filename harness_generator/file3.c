int get_value_at_index(int index) {
    static int arr[5] = {10, 20, 30, 40, 50};
    return arr[index];
}

int add(int a, int b){
    int result = a + b;
    if(result > 10){
        result = 10;
    }
    return result;
}

int subtract(int a, int b) {
    return a - b;
}

void hello() {
    printf("Hello, world!\n");
}