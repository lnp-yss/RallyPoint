from pathlib import Path
import sqlite3
import pytest
from scripts.backup_scoreboard import backup, verify
from pickleball_scoreboard.scoring.scoring_engine import ScoringEngine
from pickleball_scoreboard.config import Settings


def test_backup_and_isolated_restore(tmp_path):
    root=tmp_path/'application'
    (root/'pickleball_scoreboard').mkdir(parents=True)
    (root/'pickleball_scoreboard/app.py').write_text('# app')
    (root/'data').mkdir()
    with sqlite3.connect(root/'data/club.db') as db:
        db.execute('CREATE TABLE test (value TEXT)')
        db.execute("INSERT INTO test VALUES ('preserved')")
    target=backup(root,tmp_path/'backups')
    verify(target)
    with (target/'application.tar.gz').open('ab') as archive:
        archive.write(b'corrupt')
    with pytest.raises(ValueError):
        verify(target)


def test_correct_finished_result_undo():
    engine=ScoringEngine(Settings(best_of=1))
    engine.apply('DECLARE_WINNER',{'winner':'A','scores':{'A':7,'B':6}})
    before=engine.state.to_dict()
    engine.apply('CORRECT_RESULT',{'winner':'B','scores':{'A':6,'B':8}})
    assert engine.state.winner=='B'
    assert engine.state.completed_games==[{'A':6,'B':8}]
    engine.apply('TEAM_A_UNDO')
    assert engine.state.to_dict()==before


def test_backup_ignores_gpio_fifo(tmp_path):
    import os
    if not hasattr(os,'mkfifo'):
        pytest.skip('Unix runtime FIFO')
    root=tmp_path/'app'
    (root/'pickleball_scoreboard').mkdir(parents=True)
    (root/'pickleball_scoreboard/app.py').write_text('# app')
    os.mkfifo(root/'.lgd-nfy0')
    target=backup(root,tmp_path/'backups')
    verify(target)
