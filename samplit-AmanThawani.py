import random
import sys

def decide(prob):
    x = random.random()
    return x < prob

if __name__ == "__main__":
    prob = 0.01
    with open(sys.argv[1], encoding="utf-8") as f:
        for line in f:
            if decide(prob):
                print(line, end="")