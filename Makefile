IMAGE ?= pdfshrink:latest
FILE  ?=

.PHONY: build test shrink shell clean help

help:           ## Показать справку
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

build:          ## Собрать Docker-образ
	docker build -t $(IMAGE) .

test:           ## Прогнать тесты (локально, нужен pytest)
	python -m pytest

shrink:         ## Сжать файл: make shrink FILE=report.pdf
	@test -n "$(FILE)" || (echo "Укажите FILE=имя.pdf"; exit 1)
	docker run --rm --user "$$(id -u):$$(id -g)" -v "$$PWD:/data" $(IMAGE) "$(FILE)" -v

shell:          ## Разовый контейнер с shell для отладки
	docker run --rm -it --entrypoint bash -v "$$PWD:/data" $(IMAGE)

clean:          ## Удалить образ и мусор
	-docker rmi $(IMAGE)
	rm -rf .pytest_cache **/__pycache__
