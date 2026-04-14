# danser!droid — инструкция по сборке .exe

## Установка зависимостей:

```
pip install customtkinter opencv-python numpy osudroid-api-wrapper pyinstaller Pillow
```

## Запуск без сборки.exe (для теста):

```
python app.py
```

## Сборка в .exe:

```
pyinstaller --onefile --windowed --name "danser!droid" app.py
```
Примечание: создайте папку assets перед выполнением вышеуказанной команды. 

## Итоговый результат должен получиться таким:
```


      danser!droid/
        ├── assets/
        ├── build/
        ├── dist/
        ├── app.py
        ├── README.md
        ├── danser!droid.spec
        └── parse.osu/py
```
Папки создаются автоматически при первом запуске.
