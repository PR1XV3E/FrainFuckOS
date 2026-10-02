import sys
import ast
import shlex

def compile_print(text):
    bf = "[-]"
    current = 0

    for char in text:
        target = ord(char)
        delta = target - current

        if delta > 0:
            bf += "+" * delta
        elif delta < 0:
            bf += "-" * (-delta)

        bf += "."
        current = target

    return bf

def move_pointer(output, current, target):
    diff = target - current

    if diff > 0:
        output += ">" * diff
    elif diff < 0:
        output += "<" * (-diff)

    return output, target

def compile_compare_char(output, current_cell, source, char, flag, temp, scratch):
    # Clear temporary cells
    output, current_cell = move_pointer(output, current_cell, temp)
    output += "[-]"

    output, current_cell = move_pointer(output, current_cell, scratch)
    output += "[-]"

    # Copy source -> temp + scratch
    output, current_cell = move_pointer(output, current_cell, source)

    output += "["
    output += "-"

    move, _ = move_pointer("", source, temp)
    output += move + "+"

    move, _ = move_pointer("", temp, scratch)
    output += move + "+"

    move, _ = move_pointer("", scratch, source)
    output += move

    output += "]"

    # Restore original source from scratch
    output, current_cell = move_pointer(output, source, scratch)

    output += "["
    output += "-"

    move, _ = move_pointer("", scratch, source)
    output += move + "+"

    move, _ = move_pointer("", source, scratch)
    output += move

    output += "]"

    # Compare temp against expected character
    output, current_cell = move_pointer(output, scratch, temp)

    output += "-" * ord(char)

    # Nonzero = mismatch
    output += "["

    move, _ = move_pointer("", temp, flag)
    output += move
    output += "[-]"

    move, _ = move_pointer("", flag, temp)
    output += move

    output += "[-]"
    output += "]"

    # Return to original source
    output, current_cell = move_pointer(output, temp, source)

    return output, current_cell

def compile_copy_cell(output, current_cell, source, dest, scratch):
    # Clear destination
    output, current_cell = move_pointer(
        output, current_cell, dest
    )
    output += "[-]"

    # Clear scratch
    output, current_cell = move_pointer(
        output, current_cell, scratch
    )
    output += "[-]"

    # source -> dest + scratch
    output, current_cell = move_pointer(
        output, current_cell, source
    )

    output += "["
    output += "-"

    move, _ = move_pointer("", source, dest)
    output += move + "+"

    move, _ = move_pointer("", dest, scratch)
    output += move + "+"

    move, _ = move_pointer("", scratch, source)
    output += move

    output += "]"

    # Restore source from scratch
    output, current_cell = move_pointer(
        output, source, scratch
    )

    output += "["
    output += "-"

    move, _ = move_pointer("", scratch, source)
    output += move + "+"

    move, _ = move_pointer("", source, scratch)
    output += move

    output += "]"

    output, current_cell = move_pointer(
        output, scratch, source
    )

    return output, current_cell

def compile_check_zero(output, current_cell, source, flag, temp, scratch):
    # Copy source into temp without destroying source
    output, current_cell = compile_copy_cell(
        output,
        current_cell,
        source,
        temp,
        scratch
    )

    # Check the temporary copy
    output, current_cell = move_pointer(
        output,
        current_cell,
        temp
    )

    output += "["

    # Nonzero means the zero-check failed:
    # clear the match flag.
    move, _ = move_pointer("", temp, flag)
    output += move
    output += "[-]"

    move, _ = move_pointer("", flag, temp)
    output += move

    # Clear temp so this executes once
    output += "[-]"

    output += "]"

    # Return to original source position
    output, current_cell = move_pointer(
        output,
        temp,
        source
    )

    return output, current_cell

def compile_file(source):
    output = ""
    lines = source.splitlines()
    i = 0
    loop_stack = []
    cells = {}
    sizes = {}

    next_cell = 0
    current_cell = 0

    valid_bf = set("><+-.,[]")

    while i < len(lines):
        raw_line = lines[i]
        line_number = i + 1
        line = raw_line.strip()

        if not line:
            i += 1
            continue

        if line.startswith("#"):
            i += 1
            continue

        # Multiline raw Brainfuck block
        if line == "BF_BEGIN":
            i += 1
            block = ""

            while i < len(lines):
                inner = lines[i].strip()

                if inner == "BF_END":
                    break

                # Ignore whitespace and comments inside BF block
                for char in inner:
                    if char in valid_bf:
                        block += char

                i += 1

            if i >= len(lines):
                raise SyntaxError(
                    f"Line {line_number}: BF_BEGIN without BF_END"
                )

            output += block
            i += 1
            continue

        # Single-line raw BF
        if line.startswith("BF "):
            value = line[3:].strip()

            try:
                code = ast.literal_eval(value)
            except Exception:
                raise SyntaxError(
                    f"Line {line_number}: invalid BF string"
                )

            if not isinstance(code, str):
                raise SyntaxError(
                    f"Line {line_number}: BF needs a string"
                )

            if any(char not in valid_bf for char in code):
                raise SyntaxError(
                    f"Line {line_number}: BF contains invalid characters"
                )

            output += code
            i += 1
            continue

        if line == "SHELL":
            # Native BIOS services, called by the Brainfuck kernel event loop.
            output += "!"
            i += 1
            continue

        if line == "ZERO":
            output += "[-]"
            i += 1
            continue

        if line == "OUT":
            output += "."
            i += 1
            continue

        if line == "IN":
            output += ","
            i += 1
            continue

        if line.startswith("COPY "):
            parts = line.split()

            if len(parts) != 4:
                raise SyntaxError(
                    f"Line {line_number}: use COPY source destination scratch"
                )

            _, source_name, dest_name, scratch_name = parts

            for name in (source_name, dest_name, scratch_name):
                if name not in cells:
                    raise SyntaxError(
                        f"Line {line_number}: unknown cell '{name}'"
                    )

            source = cells[source_name]
            dest = cells[dest_name]
            scratch = cells[scratch_name]

            if len({source, dest, scratch}) != 3:
                raise SyntaxError(
                    f"Line {line_number}: COPY needs 3 different cells"
                )

            # Clear destination
            output, current_cell = move_pointer(
                output, current_cell, dest
            )
            output += "[-]"

            # Clear scratch
            output, current_cell = move_pointer(
                output, current_cell, scratch
            )
            output += "[-]"

            # Go to source
            output, current_cell = move_pointer(
                output, current_cell, source
            )

            # source -> destination + scratch
            output += "["

            output += "-"

            move, _ = move_pointer("", source, dest)
            output += move
            output += "+"

            move, _ = move_pointer("", dest, scratch)
            output += move
            output += "+"

            move, _ = move_pointer("", scratch, source)
            output += move

            output += "]"

            # Restore source from scratch
            output, current_cell = move_pointer(
                output, source, scratch
            )

            output += "["

            output += "-"

            move, _ = move_pointer("", scratch, source)
            output += move
            output += "+"

            move, _ = move_pointer("", source, scratch)
            output += move

            output += "]"

            # Finish back at source
            output, current_cell = move_pointer(
                output, scratch, source
            )

            i += 1
            continue

        if line.startswith("COMPARECHAR "):
            parts = shlex.split(line)

            if len(parts) != 5:
                raise SyntaxError(
                    f'Line {line_number}: use COMPARECHAR "x" flag temp scratch'
                )

            _, char, flag_name, temp_name, scratch_name = parts

            if len(char) != 1:
                raise SyntaxError(
                    f"Line {line_number}: COMPARECHAR needs one character"
                )

            for name in (flag_name, temp_name, scratch_name):
                if name not in cells:
                    raise SyntaxError(
                        f"Line {line_number}: unknown cell '{name}'"
                    )

            source = current_cell
            flag = cells[flag_name]
            temp = cells[temp_name]
            scratch = cells[scratch_name]

            output, current_cell = compile_compare_char(
                output,
                current_cell,
                source,
                char,
                flag,
                temp,
                scratch
            )

            i += 1
            continue

        if line.startswith("CHECKZERO "):
            parts = line.split()
            if len(parts) != 4 or any(name not in cells for name in parts[1:]):
                raise SyntaxError(f"Line {line_number}: use CHECKZERO flag temp scratch")
            flag, temp, scratch = (cells[name] for name in parts[1:])
            if len({current_cell, flag, temp, scratch}) != 4:
                raise SyntaxError(f"Line {line_number}: CHECKZERO needs distinct cells")
            output, current_cell = compile_check_zero(
                output, current_cell, current_cell, flag, temp, scratch
            )
            i += 1
            continue

        if line.startswith("MATCHSTRING "):
            parts = shlex.split(line)

            if len(parts) != 6:
                raise SyntaxError(
                    f'Line {line_number}: use MATCHSTRING "text" start flag temp scratch'
                )

            _, text, start_name, flag_name, temp_name, scratch_name = parts

            for name in (start_name, flag_name, temp_name, scratch_name):
                if name not in cells:
                    raise SyntaxError(
                        f"Line {line_number}: unknown cell '{name}'"
                    )

            start = cells[start_name]
            flag = cells[flag_name]
            temp = cells[temp_name]
            scratch = cells[scratch_name]

            if len(text) > sizes[start_name]:
                raise SyntaxError(
                    f"Line {line_number}: MATCHSTRING text is larger than '{start_name}'"
                )

            for offset, char in enumerate(text):
                source = start + offset

                output, current_cell = compile_compare_char(
                    output,
                    current_cell,
                    source,
                    char,
                    flag,
                    temp,
                    scratch
                )

            i += 1
            continue

        if line.startswith("SETCHAR "):
            value = line[8:].strip()

            try:
                char = ast.literal_eval(value)
            except Exception:
                raise SyntaxError(
                    f"Line {line_number}: invalid SETCHAR value"
                )

            if not isinstance(char, str) or len(char) != 1:
                raise SyntaxError(
                    f"Line {line_number}: SETCHAR needs exactly one character"
                )

            output += "[-]"
            output += "+" * ord(char)

            i += 1
            continue

        if line.startswith("SET "):
            value = int(line[4:].strip())

            if value < 0 or value > 255:
                raise SyntaxError(
                    f"Line {line_number}: SET must be between 0 and 255"
                )

            output += "[-]"
            output += "+" * value

            i += 1
            continue

        if line.startswith("ADD "):
            amount = int(line[4:].strip())
            output += "+" * amount
            i += 1
            continue

        if line.startswith("SUB "):
            amount = int(line[4:].strip())
            output += "-" * amount
            i += 1
            continue

        if line.startswith("RIGHT "):
            amount = int(line[6:].strip())

            if amount < 0:
                raise SyntaxError(
                    f"Line {line_number}: RIGHT cannot be negative"
                )

            output += ">" * amount
            current_cell += amount

            i += 1
            continue

        if line.startswith("LEFT "):
            amount = int(line[5:].strip())

            if amount < 0:
                raise SyntaxError(
                    f"Line {line_number}: LEFT cannot be negative"
                )

            if current_cell - amount < 0:
                raise SyntaxError(
                    f"Line {line_number}: LEFT would move before cell 0"
                )

            output += "<" * amount
            current_cell -= amount

            i += 1
            continue

        if line == "LOOP":
            # Remember which cell controls this loop
            loop_stack.append(current_cell)

            output += "["

            i += 1
            continue

        if line == "END":
            if not loop_stack:
                raise SyntaxError(
                    f"Line {line_number}: END without LOOP"
                )

            # Brainfuck ] must execute on the same
            # cell where [ began.
            loop_cell = loop_stack.pop()

            output, current_cell = move_pointer(
                output,
                current_cell,
                loop_cell
            )

            output += "]"

            i += 1
            continue

        if line.startswith("ARRAY "):
            parts = line.split()

            if len(parts) != 3:
                raise SyntaxError(
                    f"Line {line_number}: use ARRAY name size"
                )

            _, name, size_text = parts

            try:
                size = int(size_text)
            except ValueError:
                raise SyntaxError(
                    f"Line {line_number}: ARRAY size must be a number"
                )

            if size <= 0:
                raise SyntaxError(
                    f"Line {line_number}: ARRAY size must be positive"
                )

            if name in cells:
                raise SyntaxError(
                    f"Line {line_number}: '{name}' already exists"
                )

            # Name points to first cell
            cells[name] = next_cell
            sizes[name] = size

            # Reserve the whole block
            next_cell += size

            i += 1
            continue

        if line.startswith("CELL "):
            name = line[5:].strip()

            if not name:
                raise SyntaxError(
                    f"Line {line_number}: CELL needs a name"
                )

            if name in cells:
                raise SyntaxError(
                    f"Line {line_number}: cell '{name}' already exists"
                )

            cells[name] = next_cell
            sizes[name] = 1

            next_cell += 1

            i += 1
            continue

        if line.startswith("GOTOITEM "):
            parts = line.split()

            if len(parts) != 3:
                raise SyntaxError(
                    f"Line {line_number}: use GOTOITEM array index"
                )

            _, name, index_text = parts

            if name not in cells:
                raise SyntaxError(
                    f"Line {line_number}: unknown array '{name}'"
                )

            try:
                index = int(index_text)
            except ValueError:
                raise SyntaxError(
                    f"Line {line_number}: array index must be a number"
                )

            if index < 0 or index >= sizes[name]:
                raise SyntaxError(
                    f"Line {line_number}: index {index} is outside '{name}'"
                )

            target = cells[name] + index

            output, current_cell = move_pointer(
                output,
                current_cell,
                target
            )

            i += 1
            continue

        if line.startswith("GOTO "):
            name = line[5:].strip()

            if name not in cells:
                raise SyntaxError(
                    f"Line {line_number}: unknown cell '{name}'"
                )

            target = cells[name]
            difference = target - current_cell

            if difference > 0:
                output += ">" * difference
            elif difference < 0:
                output += "<" * (-difference)

            current_cell = target

            i += 1
            continue

        if line.startswith("READLINE "):
            parts = line.split()

            if len(parts) != 2:
                raise SyntaxError(
                    f"Line {line_number}: use READLINE array"
                )

            _, buffer_name = parts

            if buffer_name not in cells:
                raise SyntaxError(
                    f"Line {line_number}: unknown array '{buffer_name}'"
                )

            buffer_start = cells[buffer_name]
            buffer_size = sizes[buffer_name]

            # Internal READLINE cells
            input_cell     = next_cell
            alive_cell     = next_cell + 1
            enter_flag     = next_cell + 2
            backspace_flag = next_cell + 3
            normal_flag    = next_cell + 4
            temp_cell      = next_cell + 5
            scratch_cell   = next_cell + 6

            # One-hot cursor:
            #
            # marker 0 = cursor before first character
            # marker 1 = after first character
            # ...
            # marker N = buffer full
            marker_start = next_cell + 7

            next_cell += 7 + (buffer_size + 1)

            # ---------------------------------
            # Clear command buffer
            # ---------------------------------

            for offset in range(buffer_size):
                output, current_cell = move_pointer(
                    output,
                    current_cell,
                    buffer_start + offset
                )

                output += "[-]"

            # ---------------------------------
            # Clear cursor markers
            # ---------------------------------

            for offset in range(buffer_size + 1):
                output, current_cell = move_pointer(
                    output,
                    current_cell,
                    marker_start + offset
                )

                output += "[-]"

            # Cursor starts at position 0
            output, current_cell = move_pointer(
                output,
                current_cell,
                marker_start
            )

            output += "+"

            # alive = 1
            output, current_cell = move_pointer(
                output,
                current_cell,
                alive_cell
            )

            output += "[-]+"

            # =================================
            # INPUT LOOP
            # =================================

            output += "["

            # Read key WITHOUT echoing yet.
            output, current_cell = move_pointer(
                output,
                alive_cell,
                input_cell
            )

            output += "[-],"

            # ---------------------------------
            # Is it Enter?
            # ---------------------------------

            output, current_cell = move_pointer(
                output,
                current_cell,
                enter_flag
            )

            output += "[-]+"

            output, current_cell = move_pointer(
                output,
                current_cell,
                input_cell
            )

            output, current_cell = compile_compare_char(
                output,
                current_cell,
                input_cell,
                "\r",
                enter_flag,
                temp_cell,
                scratch_cell
            )

            # ---------------------------------
            # Is it Backspace?
            # ---------------------------------

            output, current_cell = move_pointer(
                output,
                current_cell,
                backspace_flag
            )

            output += "[-]+"

            output, current_cell = move_pointer(
                output,
                current_cell,
                input_cell
            )

            output, current_cell = compile_compare_char(
                output,
                current_cell,
                input_cell,
                "\x08",
                backspace_flag,
                temp_cell,
                scratch_cell
            )

            # Assume normal character
            output, current_cell = move_pointer(
                output,
                current_cell,
                normal_flag
            )

            output += "[-]+"

            # =================================
            # ENTER
            # =================================

            output, current_cell = move_pointer(
                output,
                current_cell,
                enter_flag
            )

            output += "["

            # Run once
            output += "[-]"

            # Echo Enter.
            # stage2 turns CR into CR+LF.
            output, current_cell = move_pointer(
                output,
                enter_flag,
                input_cell
            )

            output += "."

            # Stop READLINE
            output, current_cell = move_pointer(
                output,
                current_cell,
                alive_cell
            )

            output += "[-]"

            # Prevent normal-character handler
            output, current_cell = move_pointer(
                output,
                current_cell,
                normal_flag
            )

            output += "[-]"

            # Prevent Backspace handler
            output, current_cell = move_pointer(
                output,
                current_cell,
                backspace_flag
            )

            output += "[-]"

            # Return to Enter flag for ]
            output, current_cell = move_pointer(
                output,
                current_cell,
                enter_flag
            )

            output += "]"

            # =================================
            # BACKSPACE
            # =================================

            output, current_cell = move_pointer(
                output,
                current_cell,
                backspace_flag
            )

            output += "["

            # Run once
            output += "[-]"

            # Not a normal character
            output, current_cell = move_pointer(
                output,
                backspace_flag,
                normal_flag
            )

            output += "[-]"

            #
            # Scan cursor positions 1..N.
            #
            # Ascending order is important:
            # moving the cursor left creates a marker
            # we've already scanned.
            #

            for index in range(1, buffer_size + 1):
                marker = marker_start + index
                previous_marker = marker_start + index - 1
                previous_char = buffer_start + index - 1

                output, current_cell = move_pointer(
                    output,
                    current_cell,
                    marker
                )

                output += "["

                # Remove current marker
                output += "[-]"

                # Echo Backspace ONLY when there
                # really is a character to erase.
                output, current_cell = move_pointer(
                    output,
                    marker,
                    input_cell
                )

                output += "."

                # Move cursor left
                output, current_cell = move_pointer(
                    output,
                    current_cell,
                    previous_marker
                )

                output += "[-]+"

                # Clear removed character
                output, current_cell = move_pointer(
                    output,
                    current_cell,
                    previous_char
                )

                output += "[-]"

                # Return to this marker
                output, current_cell = move_pointer(
                    output,
                    current_cell,
                    marker
                )

                output += "]"

            # Return to Backspace flag
            output, current_cell = move_pointer(
                output,
                current_cell,
                backspace_flag
            )

            output += "]"

            # =================================
            # NORMAL CHARACTER
            # =================================

            output, current_cell = move_pointer(
                output,
                current_cell,
                normal_flag
            )

            output += "["

            # Run once
            output += "[-]"

            #
            # Search cursor from right to left.
            #
            # This is important because setting
            # marker[index + 1] must NOT trigger
            # another write during this scan.
            #

            for index in reversed(range(buffer_size)):
                marker = marker_start + index
                next_marker = marker_start + index + 1
                target = buffer_start + index

                output, current_cell = move_pointer(
                    output,
                    current_cell,
                    marker
                )

                output += "["

                # Consume cursor marker
                output += "[-]"

                # Copy input into buffer slot
                output, current_cell = compile_copy_cell(
                    output,
                    marker,
                    input_cell,
                    target,
                    scratch_cell
                )

                # Echo accepted character
                output += "."

                # Advance cursor
                output, current_cell = move_pointer(
                    output,
                    current_cell,
                    next_marker
                )

                output += "[-]+"

                # Return to original marker
                output, current_cell = move_pointer(
                    output,
                    current_cell,
                    marker
                )

                output += "]"

            # If cursor == marker N (buffer full),
            # nothing above runs. Extra characters
            # are simply ignored.

            output, current_cell = move_pointer(
                output,
                current_cell,
                normal_flag
            )

            output += "]"

            # Return to alive cell for outer loop
            output, current_cell = move_pointer(
                output,
                current_cell,
                alive_cell
            )

            output += "]"

            # READLINE finishes at command[0]
            output, current_cell = move_pointer(
                output,
                current_cell,
                buffer_start
            )

            i += 1
            continue

        if line.startswith("PRINTLN "):
            value = line[8:].strip()

            try:
                text = ast.literal_eval(value)
            except Exception:
                raise SyntaxError(
                    f"Line {line_number}: invalid PRINTLN string"
                )

            if not isinstance(text, str):
                raise SyntaxError(
                    f"Line {line_number}: PRINTLN needs a string"
                )

            output += compile_print(text + "\r")
            i += 1
            continue

        if line.startswith("PRINT "):
            value = line[6:].strip()

            try:
                text = ast.literal_eval(value)
            except Exception:
                raise SyntaxError(
                    f"Line {line_number}: invalid PRINT string"
                )

            if not isinstance(text, str):
                raise SyntaxError(
                    f"Line {line_number}: PRINT needs a string"
                )

            output += compile_print(text)
            i += 1
            continue

        raise SyntaxError(
            f"Line {line_number}: unknown instruction: {line}"
        )

    if loop_stack:
        raise SyntaxError(
            f"{len(loop_stack)} unclosed LOOP block(s)"
        )

    return output


if len(sys.argv) != 3:
    print("Usage: python bfasm.py input.bfs output.bf")
    sys.exit(1)


input_file = sys.argv[1]
output_file = sys.argv[2]

with open(input_file, "r", encoding="utf-8") as f:
    source = f.read()

brainfuck = compile_file(source)

with open(output_file, "w", encoding="ascii") as f:
    f.write(brainfuck)

print(f"Compiled {input_file} -> {output_file}")
print(f"{len(brainfuck)} Brainfuck instructions")
