"""EchoSpeech command set (Zhang et al., CHI '23, Table 1): 31 commands, 32 word labels."""

COMMANDS = {
    "music": ["Play", "Stop", "Resume", "Pause", "Previous", "Next", "Volume"],
    "smart devices": ["Left", "Right", "Up", "Down", "OK", "Cancel", "Menu", "Dial", "Hang up", "Open", "Close"],
    "digits": ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"],
    "assistants": ["Hey Google", "Hey Siri", "Alexa"],
}
ALL = [c for group in COMMANDS.values() for c in group]
assert len(ALL) == 31


def words(command):
    """Word labels for CTC: multi-word commands split into words (W = 32 for this set)."""
    return command.lower().split()


VOCAB = sorted({w for c in ALL for w in words(c)})
assert len(VOCAB) == 32, len(VOCAB)
