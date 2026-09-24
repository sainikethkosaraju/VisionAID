COMP := ../../components
CFLAGS ?= -std=c99 -O1 -g -Wall -Wextra -Werror -Wshadow -Wconversion -Wno-sign-conversion \
          -fsanitize=address,undefined -fno-omit-frame-pointer
INCLUDES := -I$(COMP)/vision_buffer/include -I$(COMP)/fall_trigger/include
SRCS := test_main.c $(COMP)/vision_buffer/vision_buffer.c $(COMP)/fall_trigger/fall_trigger.c

.PHONY: test clean
test: test_runner
	./test_runner

test_runner: $(SRCS)
	$(CC) $(CFLAGS) $(INCLUDES) $(SRCS) -lm -o $@

clean:
	rm -f test_runner
