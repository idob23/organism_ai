# Графики и диаграммы — как строить через matplotlib

matplotlib уже установлен в sandbox.

## Базовые типы графиков

### Столбчатая диаграмма (для сравнения)
```python
import matplotlib
matplotlib.use('Agg')  # без GUI
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

fig, ax = plt.subplots(figsize=(10, 6))
ax.bar(categories, values, color='#1E3A5F', edgecolor='white', linewidth=0.5)
ax.set_title('Название', fontsize=14, fontweight='bold', pad=15)
ax.set_xlabel('Ось X', fontsize=11)
ax.set_ylabel('Ось Y', fontsize=11)
ax.grid(axis='y', alpha=0.3, linestyle='--')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig('/output/chart.png', dpi=150, bbox_inches='tight')
print("Saved files: chart.png")
```

### Линейный график (для динамики)
```python
ax.plot(x, y, color='#1E3A5F', linewidth=2, marker='o', markersize=4)
ax.fill_between(x, y, alpha=0.1, color='#1E3A5F')
```

## Правила
- Всегда Agg backend (нет GUI в sandbox)
- Основной цвет: #1E3A5F (тёмно-синий)
- Акцентный цвет: #E74C3C (красный для предупреждений)
- dpi=150 для чёткости в Telegram
- Убирать верхний и правый border (spines)
- Сохранять в /output/, print "Saved files: filename.png"
