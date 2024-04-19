from pathlib import Path
import shutil
import sys
from enum import Enum

from util import ProgressBar
import config
from colorama import Fore, Style


class Verdict(Enum):
    """The verdict of a testcase or testgroup"""

    ACCEPTED = 1
    WRONG_ANSWER = 2
    TIME_LIMIT_EXCEEDED = 3
    RUNTIME_ERROR = 4
    VALIDATOR_CRASH = 5
    COMPILER_ERROR = 6

    def __str__(self):
        return {
            Verdict.ACCEPTED: 'ACCEPTED',
            Verdict.WRONG_ANSWER: 'WRONG ANSWER',
            Verdict.TIME_LIMIT_EXCEEDED: 'TIME LIMIT EXCEEDED',
            Verdict.RUNTIME_ERROR: 'RUNTIME ERROR',
            Verdict.VALIDATOR_CRASH: 'VALIDATOR CRASH',
            Verdict.COMPILER_ERROR: 'COMPILER ERROR',
        }[self]

    def color(self):
        return {
            Verdict.ACCEPTED: Fore.GREEN,
            Verdict.WRONG_ANSWER: Fore.RED,
            Verdict.TIME_LIMIT_EXCEEDED: Fore.MAGENTA,
            Verdict.RUNTIME_ERROR: Fore.YELLOW,
            Verdict.VALIDATOR_CRASH: Fore.RED,
            Verdict.COMPILER_ERROR: Fore.RED,
        }[self]


def to_char(v: Verdict | None, lower=False):
    if v is None:
        return f'{Fore.BLUE}?{Style.RESET_ALL}'
    else:
        char = str(v)[0].lower() if lower else str(v)[0].upper()
        return f'{v.color()}{char}{Style.RESET_ALL}'


def to_string(v: Verdict | None):
    if v is None:
        return to_char(v)
    else:
        return f'{v.color()}{str(v)}{Style.RESET_ALL}'


def from_string(s: str) -> Verdict:
    match s:
        case 'CORRECT' | 'ACCEPTED' | 'AC':
            return Verdict.ACCEPTED
        case 'WRONG-ANSWER' | 'WRONG_ANSWER' | 'WA':
            return Verdict.WRONG_ANSWER
        case 'TIMELIMIT' | 'TIME_LIMIT_EXCEEDED' | 'TLE':
            return Verdict.TIME_LIMIT_EXCEEDED
        case 'RUN-ERROR' | 'RUN_TIME_ERROR' | 'RUNTIME_ERROR' | 'RTE':
            return Verdict.RUNTIME_ERROR
        case 'NO-OUTPUT':
            return Verdict.WRONG_ANSWER
        case 'COMPILER-ERROR':
            return Verdict.COMPILER_ERROR
        case 'CHECK-MANUALLY':
            raise NotImplementedError
        case _:
            raise ValueError(f"Unknown verdict string {s}")


def from_string_domjudge(s: str) -> Verdict:
    match s:
        case 'CORRECT' | 'ACCEPTED':
            return Verdict.ACCEPTED
        case 'WRONG-ANSWER' | 'WRONG_ANSWER':
            return Verdict.WRONG_ANSWER
        case 'TIMELIMIT' | 'TIME_LIMIT_EXCEEDED':
            return Verdict.TIME_LIMIT_EXCEEDED
        case 'RUN-ERROR' | 'RUN_TIME_ERROR':
            return Verdict.RUNTIME_ERROR
        case 'NO-OUTPUT':
            return Verdict.WRONG_ANSWER
        case 'COMPILER-ERROR':
            return Verdict.COMPILER_ERROR
        case 'CHECK-MANUALLY':
            raise NotImplementedError
        case _:
            raise ValueError(f"Unknown DOMjudge verdict string {s}")


class Verdicts:
    """The verdicts of a submission.

    Testcases and testgroups are identified by strings.  In particular,
    * the testcase whose input file is 'a/b/1.in' is called 'a/b/1'
    * the two topmost testgroups are 'sample', 'secret'
    * the root is called '.'

    Initialised with all testcases. Individual verdicts are registered
    with __setitem__, which infers verdicts upwards in the tree as they become
    available (and retuns the topmost inferred testgroup).
    Verdicts (registered and inferred) are accessed with __getitem__

    >>> V = Verdicts(["a/b/1", "a/b/2", "a/c/1", "a/d/1", "b/3"])
    >>> V['a/b/1'] = 'ACCEPTED'
    >>> V['a/b/2'] = 'AC' # returns 'a/b' because that verdict will be set as well
    >>> print(V['a/b'], V['.'])
    ACCEPTED None

    Attributes:
    - duration[testcase]: the duration of the testcase
    - children[testgroup]: the set of children of the given testnode
    - first_error[testgroup]: first child with non-ACCEPTED verdict; None if none exists
    - first_unknown[testgroup]: first child whose verdict is not (yet) known; None if none exists

    - _verdict[testnode]: the verdict at the given testnode, or None. In particular,
        verdict['.'] is the root verdict, sometimes called final verdict or submission verdict.
        Should not be directly set; use __setitem__ on the Verdict object instead.
    - _unknowns[testgroup]: iterator over the children that do not (yet) have a verdict,
        in lexicographic order
    """

    def __init__(self, testcase_list: list[str]):
        testcases = set(testcase_list)
        testgroups: set[str] = set(str(path) for tc in testcases for path in Path(tc).parents)
        self._verdict: dict[str, Verdict | None] = {g: None for g in testcases | testgroups}
        self.duration: dict[str, int | None] = {g: None for g in testcases}

        self.children: dict[str, set[str]] = {node: set() for node in testgroups}
        for node in testcases | testgroups:
            if node != '.':
                parent = str(Path(node).parent)
                self.children[parent].add(node)
        self.first_error: dict[str, str | None] = {node: None for node in testgroups}
        self.num_unknowns: dict[str, int] = {node: len(self.children[node]) for node in testgroups}
        self._unknowns = {node: self.unknowns_iterator(node) for node in testgroups}
        self.first_unknown: dict[str, str | None] = {
            node: next(self._unknowns[node]) for node in testgroups
        }

    def unknowns_iterator(self, node):
        """Yield the node's (yet) unknown children in lexicographic order."""
        for child in sorted(self.children[node]):
            if self._verdict[child] is not None:
                continue
            yield child

    def is_testgroup(self, node) -> bool:
        """Is the given testnode name a testgroup (rather than a testcase)?
        This assumes nonempty testgroups.
        """
        return node in self.children

    def is_testcase(self, node) -> bool:
        """Is the given testnode name a testcase (rather than a testgroup)?
        This assumes nonempty testgroups.
        """
        return node not in self.children

    def __setitem__(self, testcase, verdict: str | Verdict):
        """Set the verdict of the given testcase (implying possibly others)

        verdict can be given as a Verdict or as a string using either long or
        short form ('ACCEPTED', 'AC', or Verdict.ACCEPTED).
        """

        if isinstance(verdict, str):
            verdict = from_string(verdict)
        self._set_verdict_for_node(testcase, verdict)

    def __getitem__(self, testnode) -> Verdict | None:
        return self._verdict[testnode]

    def salient_testcase(self) -> str:
        """The testcase most salient to the root verdict. If
        self['.'] == Verdict.ACCEPTED then this is the slowest testcase.
        Otherwise it is the lexicographically first testcase that was rejected."""
        match self['.']:
            case None:
                raise ValueError("Salient testcase called before submission verdict determined")
            case Verdict.ACCEPTED:
                return max((v, k) for k, v in self.duration.items())[1]
            case _:
                return min(
                    (k, v)
                    for k, v in self._verdict.items()
                    if self.is_testcase(k) and v is not Verdict.ACCEPTED
                )[0]

    def aggregate(self, testgroup: str) -> Verdict:
        """The aggregate verdict at the given testgroup.
        Computes the lexicographically first non-accepted verdict.

        Raises:
        ValueError when missing child verdicts make the result ill-defined.
            For instance, [AC, RTE, None] is fine (the result is RTE), but
            [AC, None, RTE] is not (the first error cannot be determined).
        """
        child_verdicts = list(self._verdict[c] for c in sorted(self.children[testgroup]))
        if all(v == Verdict.ACCEPTED for v in child_verdicts):
            result = Verdict.ACCEPTED
        else:
            first_error = next(v for v in child_verdicts if v != Verdict.ACCEPTED)
            if first_error is None:
                raise ValueError(f"Verdict aggregation at {testgroup} with unknown child verdicts")
            result = first_error
        return result

    def _set_verdict_for_node(self, testnode: str, verdict):
        if self._verdict[testnode] is not None:
            raise ValueError(
                f"Overwriting verdict of {testnode} to {verdict} (was {self._verdict[testnode]})"
            )
        self._verdict[testnode] = verdict
        updated_node = testnode
        if testnode != '.':
            parent = str(Path(testnode).parent)
            first_unknown = self.first_unknown[parent]
            first_error = self.first_error[parent]

            self.num_unknowns[parent] -= 1
            # possibly update first_unknown at parent
            if testnode == first_unknown:
                first_unknown = self.first_unknown[parent] = (
                    None if self.num_unknowns[parent] == 0 else next(self._unknowns[parent])
                )

            # possibly update first_error at parent
            if verdict != Verdict.ACCEPTED and (first_error is None or first_error > testnode):
                first_error = self.first_error[parent] = testnode

            # possibly update verdict at parent and escalate change upward recursively
            if self._verdict[parent] is None and (
                first_unknown is None or first_error is not None and first_error < first_unknown
            ):
                # we can infer the verdict at the parent
                self._set_verdict_for_node(parent, self.aggregate(parent))

    def as_tree(self, max_depth=None, show_root=False) -> str:
        result = []
        stack = [('.', '', '', True)]
        while stack:
            node, indent, prefix, last = stack.pop()
            result.append(
                f"{indent}{prefix}{node.split('/')[-1]}: {to_string(self._verdict[node])}"
            )
            if max_depth is not None and len(indent) >= 2 * max_depth:
                continue
            children = sorted(self.children[node], reverse=True)
            pipe = ' ' if last else '│'
            first = True
            testcases = []
            for child in children:
                if self.is_testgroup(child):
                    if first:
                        stack.append((children[0], indent + pipe + ' ', '└─', True))
                        first = False
                    else:
                        stack.append((child, indent + pipe + ' ', '├─', False))
                else:
                    testcases.append(to_char(self._verdict[child]))
            if testcases:
                edge = '└' if first else '├'
                result.append(indent + pipe + ' ' + edge + '─' + ''.join(reversed(testcases)))
        return '\n'.join(result[int(not show_root) :])


class VerdictTable:
    def __init__(
        self,
        submissions,
        testcases,
        width=ProgressBar.columns,
        height=shutil.get_terminal_size().lines,
        max_name_width=50,
    ):
        self.submissions = [
            submission.name for verdict in submissions for submission in submissions[verdict]
        ]
        self.testcases = [t.name for t in testcases]
        self.samples = {t.name for t in testcases if t.root == 'sample'}
        self.results = []
        self.current_testcases = set()
        self.name_width = min(
            max_name_width, max([len(submission) for submission in self.submissions])
        )
        self.width = width if width >= self.name_width + 2 + 10 else -1
        self.last_width = 0
        self.last_printed = []

        self.print_without_force = (
            not config.args.no_bar and config.args.overview and self.width >= 0
        )
        if self.print_without_force:
            # generate example lines for one submission
            name = 'x' * self.name_width
            lines = [f'{Style.DIM}{Fore.CYAN}{name}{Fore.WHITE}:']

            verdicts = []
            for t, testcase in enumerate(self.testcases):
                if t % 10 == 0:
                    verdicts.append([0, ''])
                verdicts[-1][0] += 1
                verdicts[-1][1] += 's' if testcase in self.samples else '-'

            printed = self.name_width + 1
            for length, tmp in verdicts:
                if printed + 1 + length > self.width:
                    lines.append(f'{str():{self.name_width+1}}')
                    printed = self.name_width + 1
                lines[-1] += f' {tmp}'
                printed += length + 1

            # dont print table if it fills to much of the screen
            self.print_without_force = len(lines) * len(self.submissions) + 5 < height
            if not self.print_without_force:
                print(
                    f'{Fore.YELLOW}WARNING: Overview too large for terminal, skipping live updates{Style.RESET_ALL}',
                    file=sys.stderr,
                )
                print(
                    *lines,
                    f'[times {len(self.submissions)}...]',
                    Style.RESET_ALL,
                    sep='\n',
                    end='\n',
                    file=sys.stderr,
                )

    def next_submission(self):
        self.results.append(dict())
        self.current_testcases = set()

    def add_testcase(self, testcase):
        self.current_testcases.add(testcase)

    def finish_testcase(self, testcase, verdict):
        self.results[-1][testcase] = verdict
        self.current_testcases.discard(testcase)

    def _clear(self, *, force=True):
        if force or self.print_without_force:
            if self.last_printed:
                actual_width = ProgressBar.columns
                lines = sum(
                    max(1, (printed + actual_width - 1) // actual_width)
                    for printed in self.last_printed
                )

                print(
                    f'\033[{lines - 1}A\r\033[0J',
                    end='',
                    flush=True,
                    file=sys.stderr,
                )

                self.last_printed = []

    def _get_verdict(self, s, testcase):
        res = Style.DIM + Fore.WHITE + '-' + Style.RESET_ALL
        if s < len(self.results) and testcase in self.results[s]:
            res = to_char(self.results[s][testcase], testcase in self.samples)
        elif s + 1 == len(self.results) and testcase in self.current_testcases:
            res = Style.DIM + to_char(None)
        return res

    def print(self, *, force=True, new_lines=2, printed_lengths=None):
        if printed_lengths is None:
            printed_lengths = []
        if force or self.print_without_force:
            printed_text = ['\n' * new_lines]
            printed_lengths += [0] * new_lines
            for s, submission in enumerate(self.submissions):
                # pad/truncate submission names to not break table layout
                name = submission
                if len(name) > self.name_width:
                    name = '...' + name[-self.name_width + 3 :]
                padding = ' ' * (self.name_width - len(name))
                printed_text.append(f'{Fore.CYAN}{name}{Style.RESET_ALL}:{padding}')

                # group verdicts in parts of length at most ten
                verdicts = []
                for t, testcase in enumerate(self.testcases):
                    if t % 10 == 0:
                        verdicts.append([0, ''])
                    verdicts[-1][0] += 1
                    verdicts[-1][1] += self._get_verdict(s, testcase)

                printed = self.name_width + 1
                for length, tmp in verdicts:
                    if self.width >= 0 and printed + 1 + length > self.width:
                        printed_text.append(f'\n{str():{self.name_width+1}}')
                        printed_lengths.append(printed)
                        printed = self.name_width + 1

                    printed_text.append(f' {tmp}')
                    printed += length + 1

                printed_lengths.append(printed)
                printed_text.append('\n')
            self._clear(force=True)
            print(''.join(printed_text), end='', flush=True, file=sys.stderr)
            self.last_printed = printed_lengths

    def ProgressBar(
        self, prefix, max_len=None, count=None, *, items=None, needs_leading_newline=False
    ):
        return TableProgressBar(
            self, prefix, max_len, count, items=items, needs_leading_newline=needs_leading_newline
        )


class TableProgressBar(ProgressBar):
    def __init__(self, table, prefix, max_len, count, *, items, needs_leading_newline):
        super().__init__(
            prefix, max_len, count, items=items, needs_leading_newline=needs_leading_newline
        )
        self.table = table

    # at the begin of any IO the progress bar locks so we can clear the table at this point
    def __enter__(self):
        super().__enter__()
        if ProgressBar.lock_depth == 1:
            self.reset_line_buffering = sys.stderr.line_buffering
            sys.stderr.reconfigure(line_buffering=False)
            self.table._clear(force=False)

    # at the end of any IO the progress bar unlocks so we can reprint the table at this point
    def __exit__(self, *args):
        if ProgressBar.lock_depth == 1:
            # ProgressBar.columns is just an educated guess for the number of printed chars
            # in the ProgressBar
            self.table.print(force=False, printed_lengths=[ProgressBar.columns])
            sys.stderr.reconfigure(line_buffering=self.reset_line_buffering)
            print(end='', flush=True, file=sys.stderr)
        super().__exit__(*args)

    def _print(self, *objects, sep='', end='\n', file=sys.stderr, flush=True):
        assert self._is_locked()
        # drop all flushes...
        print(*objects, sep=sep, end=end, file=file, flush=False)

    def start(self, item):
        self.table.add_testcase(item.testcase.name)
        return super().start(item)

    def done(self, success=True, message='', data=''):
        return super().done(success, message, data)

    def finalize(self, *, print_done=True, message=None):
        with self:
            res = super().finalize(print_done=print_done, message=message)
            self.table._clear(force=True)
            return res
