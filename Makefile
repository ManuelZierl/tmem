.PHONY: test build install uninstall

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v
	bash -n shell/tmem.bash install.sh uninstall.sh

build:
	python3 -m build

install:
	./install.sh

uninstall:
	./uninstall.sh
