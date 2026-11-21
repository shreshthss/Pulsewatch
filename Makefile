.PHONY: all test demo clean summarize analyze report

all: test

test:
	python3 -m unittest discover tests -v

demo:
	python3 demo.py

matrix:
	python3 -m traffic_lab.benchmark_matrix

clean:
	rm -rf __pycache__ */__pycache__ *.pyc */*.pyc .pytest_cache
