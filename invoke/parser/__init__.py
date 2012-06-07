import copy

from fluidity import StateMachine, state, transition
from lexicon import Lexicon

from .context import Context
from .argument import Argument # Mostly for importing via invoke.parser.<x>
from ..util import debug


class Parser(object):
    def __init__(self, contexts=(), initial=None):
        self.initial = initial
        self.contexts = Lexicon()
        for context in contexts:
            debug("Adding %s" % context)
            if not context.name:
                raise ValueError("Non-initial contexts must have names.")
            exists = "A context named/aliased %r is already in this parser!"
            if context.name in self.contexts:
                raise ValueError(exists % context.name)
            self.contexts[context.name] = context
            for alias in context.aliases:
                if alias in self.contexts:
                    raise ValueError(exists % alias)
                self.contexts.alias(alias, to=context.name)

    def parse_argv(self, argv):
        """
        Parse an argv-style token list ``argv``.

        Returns a list of ``Context`` objects matching the order they were
        found in the ``argv`` and containing ``Argument`` objects with updated
        values based on any flags given.

        Assumes any program name has already been stripped out. Good::

            Parser(...).parse_argv(['--core-opt', 'task', '--task-opt'])

        Bad::

            Parser(...).parse_argv(['invoke', '--core-opt', ...])
        """
        machine = ParseMachine(initial=self.initial, contexts=self.contexts)
        # FIXME: Why isn't there str.partition for lists? There must be a
        # better way to do this. Split argv around the double-dash remainder
        # sentinel.
        debug("Starting argv: %r" % (argv,))
        try:
            ddash = argv.index('--')
        except ValueError:
            ddash = len(argv) # No remainder == body gets all
        body = argv[:ddash]
        remainder = argv[ddash:][1:] # [1:] to strip off remainder itself
        if remainder:
            debug("Remainder: argv[%r:][1:] => %r" % (ddash, remainder))
        for index, token in enumerate(body):
            # Handle non-space-delimited forms, if not currently expecting a
            # flag value.
            if not machine.waiting_for_flag_value and token.startswith('-'):
                orig = token
                # Equals-sign-delimited flags, eg --foo=bar or -f=bar
                if '=' in token:
                    token, _, value = token.partition('=')
                    debug("Splitting %r into tokens %r and %r" % (orig, token, value))
                    body.insert(index + 1, value)
                # Contiguous boolean short flags, e.g. -qv
                elif not token.startswith('--') and len(token) > 2:
                    rest, token = token[2:], token[:2]
                    # Handle boolean flag block vs short-flag + value
                    have_flag = token in machine.context.flags
                    if have_flag and machine.context.flags[token].takes_value:
                        body.insert(index + 1, rest)
                    else:
                        rest = map(lambda x: '-%s' % x, rest)
                        debug("Splitting %r into %r and %r" % (orig, token, rest))
                        for item in reversed(rest):
                            body.insert(index + 1, item)
            machine.handle(token)
        machine.finish()
        result = machine.result
        result.remainder = ' '.join(remainder)
        return result


class ParseMachine(StateMachine):
    initial_state = 'context'

    state('context', enter=['complete_flag', 'complete_context'])
    state('end', enter=['complete_flag', 'complete_context'])

    transition(from_='context', event='finish', to='end')
    transition(from_='context', event='see_context', action='switch_to_context', to='context')
    transition(from_='context', event='see_flag', action='switch_to_flag', to='context')

    def changing_state(self, from_, to):
        debug("ParseMachine: %r => %r" % (from_, to))

    def __init__(self, initial, contexts):
        # Initialize
        self.context = copy.deepcopy(initial)
        debug("Initialized with context: %r" % self.context)
        self.flag = None
        self.result = ParseResult()
        self.contexts = copy.deepcopy(contexts)
        debug("Available contexts: %r" % self.contexts)
        # In case StateMachine does anything in __init__
        super(ParseMachine, self).__init__()

    @property
    def waiting_for_flag_value(self):
        return self.flag and self.flag.takes_value and self.flag.raw_value is None

    def handle(self, token):
        debug("Handling token: %r" % token)
        if self.context and token in self.context.flags:
            self.see_flag(token)
        elif self.waiting_for_flag_value:
            self.see_value(token)
        elif token in self.contexts:
            self.see_context(token)
        else:
            raise ParseError("No idea what %r is!" % token)

    def complete_context(self):
        debug("Wrapping up context %r" % (self.context.name if self.context else self.context))
        if self.context and self.context not in self.result:
            self.result.append(self.context)

    def switch_to_context(self, name):
        try:
            self.context = self.contexts[name]
            debug("Moving to context %r" % name)
        except KeyError:
            raise ParseError("Task %r not found!" % name)

    def complete_flag(self):
        if self.flag is None:
            return
        if self.flag.takes_value:
            if self.flag.raw_value is None:
                raise ParseError("Flag %r needed value and was not given one!" % self.flag)
        else:
            debug("Marking seen flag %r as True" % self.flag)
            self.flag.value = True

    def switch_to_flag(self, flag):
        self.flag = self.context.flags[flag]
        debug("Moving to flag %r" % self.flag)

    def see_value(self, value):
        if self.flag.takes_value:
            debug("Setting flag %r to value %r" % (self.flag, value))
            self.flag.value = value
        else:
            raise ParseError("Flag %r doesn't take any value!" % self.flag)


class ParseResult(list):
    """
    List-like object with some extra parse-related attributes.

    Specifically, a ``.remainder`` attribute, which is a list of the tokens
    found after a ``--`` in any parsed argv list.
    """
    remainder = ""

    def to_dict(self):
        d = {}
        for context in self:
            argd = {}
            for name, arg in context.args.iteritems():
                argd[name] = arg.value
            d[context.name] = argd
        return d


class ParseError(Exception):
    pass
