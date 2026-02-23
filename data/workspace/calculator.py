def add(a, b):
    """Сложение двух чисел"""
    return a + b

def subtract(a, b):
    """Вычитание двух чисел"""
    return a - b

def multiply(a, b):
    """Умножение двух чисел"""
    return a * b

def divide(a, b):
    """Деление двух чисел"""
    if b == 0:
        return 'Ошибка: деление на ноль'
    return a / b

if __name__ == '__main__':
    a, b = 15, 7

    print('=' * 40)
    print('       ПРОСТОЙ КАЛЬКУЛЯТОР')
    print('=' * 40)
    print(f'Числа для тестирования: a = {a}, b = {b}')
    print('-' * 40)
    print(f'Сложение:    {a} + {b} = {add(a, b)}')
    print(f'Вычитание:   {a} - {b} = {subtract(a, b)}')
    print(f'Умножение:   {a} * {b} = {multiply(a, b)}')
    print(f'Деление:     {a} / {b} = {divide(a, b):.4f}')
    print('=' * 40)
