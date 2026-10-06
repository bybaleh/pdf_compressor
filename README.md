# pdfshrink — сжатие PDF до 10 МБ (и меньше) в Docker

CLI-инструмент на Python, который пересжимает PDF **до нужного размера**, а не «как получится».
Внутри — Ghostscript и qpdf; инструмент сам подбирает минимально агрессивные настройки,
при которых файл укладывается в лимит, чтобы не портить качество сильнее, чем нужно.

Контейнер **не нужно держать запущенным**: каждая команда — это разовый запуск
`docker run --rm`, который отрабатывает и сразу удаляет контейнер.

---

## Содержание

- [Как это работает](#как-это-работает)
- [Быстрый старт](#быстрый-старт)
- [Инструкция по запуску из Docker](#инструкция-по-запуску-из-docker)
  - [Linux / macOS](#linux--macos)
  - [Windows (PowerShell / CMD)](#windows-powershell--cmd)
  - [Удобный алиас](#удобный-алиас-чтобы-не-печатать-длинную-команду)
- [Все опции CLI](#все-опции-cli)
- [Примеры](#примеры)
- [Коды возврата](#коды-возврата)
- [Запуск без Docker](#запуск-без-docker)
- [Тесты](#тесты)
- [FAQ и подводные камни](#faq-и-подводные-камни)

---

## Как это работает

1. **Файл уже меньше цели?** → просто копируется, ничего не портим (отключается флагом `--force`).
2. **Сжатие без потерь (qpdf):** пересобираем потоки объектов и flate-сжатие.
   Если этого хватило — качество вообще не пострадало.
3. **Подбор профиля Ghostscript бинарным поиском** по «лестнице качества»
   (от лучшего к худшему):

   | Профиль | Preset | Изображения | Ч/Б | JPEG QFactor |
   |---|---|---|---|---|
   | `prepress-300` | /prepress | 300 dpi | 1200 dpi | 0.4 |
   | `printer-250` | /printer | 250 dpi | 1200 dpi | 0.6 |
   | `ebook-200` | /ebook | 200 dpi | 900 dpi | 0.9 |
   | `ebook-150` | /ebook | 150 dpi | 600 dpi | 1.3 |
   | `screen-120` | /screen | 120 dpi | 600 dpi | 1.7 |
   | `screen-100` | /screen | 100 dpi | 400 dpi | 2.2 |
   | `screen-72` | /screen | 72 dpi | 300 dpi | 2.6 |
   | `screen-50` | /screen | 50 dpi | 300 dpi | 3.0 |

   Бинарный поиск = максимум **3–4 запуска** Ghostscript вместо восьми,
   и выбирается **самый качественный** вариант, который влезает в лимит.
4. С флагом `--allow-grayscale` добавляются ещё 4 ступени с переводом в оттенки серого —
   последний шанс для тяжёлых цветных сканов.
5. Если цель всё равно недостижима — сохраняется самый маленький полученный вариант,
   в stderr пишется предупреждение, код возврата `1` (файл при этом не теряется).

> **Про «10 МБ».** По умолчанию цель — `10MB = 10 000 000 байт`. Это строго меньше, чем
> 10 MiB (10 485 760), поэтому результат проходит и те лимиты, где «10 Мб» считают в MiB.
> Нужны именно мебибайты — укажите `--target-size 10MiB`.

---

## Быстрый старт

```bash
git clone https://github.com/bybaleh/pdf_compressor.git
cd pdf_compressor

# 1. Собрать образ (один раз)
docker build -t pdfshrink .

# 2. Сжать файл в текущем каталоге до 10 МБ
docker run --rm -v "$PWD:/data" pdfshrink report.pdf
# → рядом появится report_compressed.pdf
```

---

## Инструкция по запуску из Docker

Идея простая: образ собирается один раз, а дальше каждая обработка — **разовый контейнер**.
Флаг `--rm` удаляет контейнер сразу после завершения команды, поэтому ничего
«висеть» в фоне не будет (`docker ps` будет пустым).

Разбор команды:

| Часть | Зачем |
|---|---|
| `docker run` | запустить контейнер |
| `--rm` | удалить контейнер сразу после работы (ничего не остаётся) |
| `-v "$PWD:/data"` | смонтировать текущий каталог хоста внутрь контейнера как `/data` |
| `--user "$(id -u):$(id -g)"` | результат будет принадлежать вам, а не root (Linux) |
| `pdfshrink` | имя образа |
| `report.pdf …` | аргументы инструмента (пути указываются **относительно текущего каталога**) |

### Linux / macOS

```bash
# Сборка образа (однократно; повторить после git pull)
docker build -t pdfshrink .

# Базовый сценарий: сжать до 10 МБ
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/data" pdfshrink report.pdf

# Своё имя результата и подробный лог попыток
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/data" pdfshrink \
  report.pdf -o report-small.pdf -v

# Другой лимит (например, 5 МБ) и перезапись исходника
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/data" pdfshrink \
  report.pdf --target-size 5MB --inplace

# Файл лежит в другом каталоге — монтируем именно его
docker run --rm --user "$(id -u):$(id -g)" -v "/home/me/Документы:/data" pdfshrink \
  "скан.pdf"

# Пакетная обработка всех PDF в каталоге (рекурсивно), результаты в out/
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/data" pdfshrink \
  . --recursive --out-dir out

# Справка
docker run --rm pdfshrink --help
```

В репозитории есть готовая обёртка, которая делает всё это за вас
(и сама собирает образ, если его ещё нет):

```bash
./pdfshrink.sh report.pdf              # цель 10 МБ
./pdfshrink.sh report.pdf -t 5MB -v    # 5 МБ + подробный лог
```

### Windows (PowerShell / CMD)

```powershell
# PowerShell
docker build -t pdfshrink .
docker run --rm -v "${PWD}:/data" pdfshrink report.pdf
docker run --rm -v "${PWD}:/data" pdfshrink report.pdf -t 5MB -v
```

```bat
:: CMD
docker run --rm -v "%cd%:/data" pdfshrink report.pdf
```

На Windows флаг `--user` не нужен — Docker Desktop сам разбирается с правами.

### Удобный алиас (чтобы не печатать длинную команду)

```bash
# добавьте в ~/.bashrc или ~/.zshrc
pdfshrink() {
  docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/data" pdfshrink:latest "$@"
}
```

После `source ~/.bashrc`:

```bash
pdfshrink report.pdf
pdfshrink scans/ -r -d out -t 8MB --allow-grayscale
```

Для PowerShell (`$PROFILE`):

```powershell
function pdfshrink { docker run --rm -v "${PWD}:/data" pdfshrink:latest @args }
```

---

## Все опции CLI

```
pdfshrink PDF [PDF ...] [опции]

  -o, --output FILE       путь результата (только для одного входного файла)
  -d, --out-dir DIR       каталог для результатов (по умолчанию — рядом с исходником)
  -t, --target-size SIZE  целевой размер: 10MB, 9.5MiB, 800k, 5000000 (по умолчанию 10MB)
      --suffix TEXT       суффикс имени результата (по умолчанию _compressed)
  -i, --inplace           перезаписать исходные файлы
      --allow-grayscale   разрешить перевод в оттенки серого, если иначе цель недостижима
      --min-dpi N         не опускать разрешение изображений ниже N dpi
  -f, --force             пересжимать, даже если файл уже меньше цели
  -r, --recursive         искать PDF в подкаталогах
      --timeout SEC       таймаут одного запуска Ghostscript (по умолчанию 900)
  -v, --verbose           подробный лог попыток
  -q, --quiet             только ошибки
      --json              машиночитаемый вывод
      --version           версия
```

---

## Примеры

**Подробный лог подбора профиля:**

```console
$ ./pdfshrink.sh scan.pdf -v
scan.pdf: исходный размер 48.20 MB, цель ≤ 10.00 MB
  lossless (qpdf): 46.90 MB
  ebook-150 (preset=/ebook, 150 dpi, mono 600 dpi, QFactor 1.3): 8.10 MB  ✓ подходит
  printer-250 (preset=/printer, 250 dpi, mono 1200 dpi, QFactor 0.6): 17.40 MB  ✗ велик
  ebook-200 (preset=/ebook, 200 dpi, mono 900 dpi, QFactor 0.9): 9.60 MB  ✓ подходит
OK scan.pdf: 48.20 MB → 9.60 MB (-80.1%, профиль ebook-200, 4 попыт., 22.3 c)
   → /data/scan_compressed.pdf
```

**Пакетно, с проверкой результата скриптом:**

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/data" pdfshrink \
  invoices/ -r -d out -t 10MB --json > report.json
jq '.results[] | select(.ok == false) | .source' report.json   # что не влезло
```

**Жёсткий лимит для почты (5 МБ), можно обесцветить:**

```bash
./pdfshrink.sh big-scan.pdf -t 5MB --allow-grayscale -v
```

**Сохранить читаемость мелкого текста (не опускаться ниже 200 dpi):**

```bash
./pdfshrink.sh contract.pdf --min-dpi 200
```

---

## Коды возврата

| Код | Значение |
|---|---|
| `0` | всё сжато до целевого размера (или уже было меньше) |
| `1` | хотя бы один файл не удалось уложить в лимит — сохранён лучший вариант |
| `2` | ошибка: файл не найден, это не PDF, Ghostscript упал, неверные аргументы |

---

## Запуск без Docker

Нужны Python ≥ 3.9, `ghostscript` и (желательно) `qpdf`:

```bash
sudo apt install ghostscript qpdf        # Debian/Ubuntu
brew install ghostscript qpdf            # macOS

python -m pdfshrink report.pdf -t 10MB
# или установить как команду:
pip install -e .
pdfshrink report.pdf -t 10MB
```

Пути к бинарникам можно переопределить переменными `GHOSTSCRIPT_BIN` и `QPDF_BIN`.

---

## Тесты

Тесты не требуют Ghostscript: он подменяется заглушкой, которая имитирует зависимость
размера от dpi, — так проверяется логика бинарного поиска, CLI и обработка ошибок.

```bash
python -m venv .venv && . .venv/bin/activate
pip install pytest
python -m pytest        # 38 тестов
```

В GitHub Actions (`.github/workflows/ci.yml`) дополнительно собирается Docker-образ
и прогоняется реальное сжатие PDF настоящим Ghostscript.

---

## FAQ и подводные камни

**Контейнер остаётся запущенным?**
Нет. `--rm` удаляет контейнер после выхода, процесс в нём один и завершается сам.
Проверить: `docker ps` — пусто. Образ (`docker images`) остаётся, это нормально,
он и нужен для быстрых запусков. Удалить: `docker rmi pdfshrink`.

**«No such file or directory» внутри контейнера.**
Контейнер видит только то, что вы смонтировали в `/data`. Запускайте команду из каталога
с PDF (`-v "$PWD:/data"`) и указывайте имя файла относительно него, без путей вида
`/home/user/...`.

**Файлы создаются от root.**
Добавьте `--user "$(id -u):$(id -g)"` (Linux). В `pdfshrink.sh` это уже сделано.

**PDF не сжимается ниже цели.**
Обычно это огромные сканы. Попробуйте по очереди: `--allow-grayscale`,
меньший `--target-size`, и помните — если в PDF 500 страниц цветных фото,
10 МБ означают очень низкое качество. Инструмент честно сообщит об этом кодом `1`.

**Текстовый PDF почти не сжался.**
Если внутри только текст и шрифты, сжимать особо нечего: выигрыш даёт только
lossless-шаг. Это ожидаемо и правильно.

**Защищённые паролем PDF** Ghostscript не откроет — сначала снимите пароль
(`qpdf --decrypt --password=... in.pdf out.pdf`).

---

## Лицензия

MIT — см. [LICENSE](LICENSE).
