#include <stdio.h>

int main() {
    char op;
    double num1, num2, result;

    printf("=== Simple Calculator ===\n");
    printf("Enter operator (+, -, *, /): ");
    scanf(" %c", &op);

    printf("Enter two numbers: ");
    scanf("%lf %lf", &num1, &num2);

    switch (op) {
        case '+': result = num1 + num2; break;
        case '-': result = num1 - num2; break;
        case '*': result = num1 * num2; break;
        case '/':
            if (num2 == 0) {
                printf("Error: Division by zero!\n");
                return 1;
            }
            result = num1 / num2;
            break;
        default:
            printf("Invalid operator!\n");
            return 1;
    }

    printf("%.2f %c %.2f = %.2f\n", num1, op, num2, result);
    return 0;
}