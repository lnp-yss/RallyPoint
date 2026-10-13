"""Commands identify rally winners; SERVICE means the named team lost a rally."""

from copy import deepcopy

from .game_state import GameState, MatchState, MatchStatus, Team
from . import sideout_scoring, rally_scoring
from ..services.event_history import EventHistory


class ScoringEngine:
    strategies = {
        "Side-Out": sideout_scoring.rally,
        "Rally": rally_scoring.rally,
    }

    def __init__(self, settings, state=None):
        self.settings = deepcopy(settings).resolve_first_serve()
        self.state = state or self.new_state()
        self.history = EventHistory()

    def new_state(self) -> MatchState:
        return MatchState(
            game=GameState(
                serving=Team(self.settings.first_serving_team),
                server=(
                    2
                    if self.settings.game_type == "Doubles"
                    and self.settings.scoring_mode == "Side-Out"
                    else 1
                ),
                first_service=self.settings.scoring_mode == "Side-Out",
            )
        )

    def restore_service_sides(self):
        """Migrate old snapshots only where their retained history proves a side."""
        previous = None
        for state in [*self.history.states, self.state]:
            game = state.game
            if game.service_side is None:
                if self.settings.game_type == "Singles" or self.settings.scoring_mode == "Rally" or game.first_service:
                    game.service_side = "Right" if game.scores[game.serving.value] % 2 == 0 else "Left"
                elif game.scores[game.serving.value] == 0:
                    game.service_side = "Right" if game.server == 1 else "Left"
                elif previous and (previous.game_number != state.game_number or previous.game.serving != game.serving):
                    game.service_side = "Right"
                elif previous and previous.game.service_side:
                    flips = game.scores[game.serving.value] - previous.game.scores[game.serving.value]
                    if previous.game.server == 1 and game.server == 2:
                        flips += 1
                    game.service_side = previous.game.service_side
                    if flips % 2:
                        game.service_side = "Left" if game.service_side == "Right" else "Right"
            previous = state

    @staticmethod
    def validate_scores(scores: dict) -> dict:
        """Require an explicit, bounded integer score for both teams."""
        if (
            not isinstance(scores, dict)
            or set(scores) != {"A", "B"}
            or any(type(v) is not int or not 0 <= v <= 999 for v in scores.values())
        ):
            raise ValueError("Enter whole-number scores from 0 to 999 for both teams")
        return dict(scores)

    def apply(self, command: str, payload: dict | None = None) -> str | None:
        payload = payload or {}
        if command == "CORRECT_RESULT":
            if self.state.status != MatchStatus.COMPLETE:
                raise ValueError("Only completed matches can be corrected")
            scores = self.validate_scores(payload.get("scores"))
            winner = payload.get("winner")
            if winner not in ("A", "B") or not self.state.completed_games:
                raise ValueError("Select a winner for a completed match")
            self.history.push(self.state)
            previous = self.state.manual_game_winner or ("A" if self.state.completed_games[-1]["A"] > self.state.completed_games[-1]["B"] else "B")
            self.state.game.scores = scores
            self.state.completed_games[-1] = dict(scores)
            if previous != winner:
                self.state.games_won[previous] = max(0, self.state.games_won[previous] - 1)
                self.state.games_won[winner] += 1
            self.state.manual_game_winner = winner
            self.state.winner = winner
            return "RESULT CORRECTED"
        if command in ("SET_SCORES", "DECLARE_WINNER"):
            if self.state.status not in (MatchStatus.PLAYING, MatchStatus.GAME_OVER):
                return None
            scores = (
                self.validate_scores(payload["scores"])
                if "scores" in payload else None
            )
            if command == "SET_SCORES":
                if scores is None:
                    raise ValueError("Both team scores are required")
                if self.state.status != MatchStatus.PLAYING:
                    raise ValueError("Only the current unfinished game can be edited")
                if scores == self.state.game.scores:
                    return "SCORES UNCHANGED"
            else:
                if payload.get("winner") not in ("A", "B"):
                    raise ValueError("Select Team A or Team B as match winner")
                if scores is not None and self.state.status != MatchStatus.PLAYING:
                    raise ValueError("Start the next game before editing its score")
            self.history.push(self.state)
            if scores is not None:
                game = self.state.game
                if game.service_side and (scores[game.serving.value] - game.scores[game.serving.value]) % 2:
                    game.service_side = "Left" if game.service_side == "Right" else "Right"
                game.scores = scores
            if command == "SET_SCORES":
                return "SCORES UPDATED"
            winner = payload["winner"]
            if self.state.status == MatchStatus.PLAYING:
                self.state.completed_games.append(dict(self.state.game.scores))
                self.state.games_won[winner] += 1
                self.state.manual_game_winner = winner
            self.state.status = MatchStatus.COMPLETE
            self.state.winner = winner
            self.state.paused = False
            return "MATCH WINNER"
        if command.endswith("_UNDO"):
            previous = self.history.pop()
            if previous is None:
                return None
            self.state = previous
            return "UNDO"
        if command == "RESET":
            self.history.push(self.state)
            self.state = self.new_state()
            return "MATCH RESET"
        if command in ("PAUSE", "RESUME"):
            if self.state.status not in (MatchStatus.PLAYING, MatchStatus.GAME_OVER):
                return None
            paused = command == "PAUSE"
            if self.state.paused == paused:
                return None
            self.history.push(self.state)
            self.state.paused = paused
            return "MATCH PAUSED" if paused else "MATCH RESUMED"
        if command == "END_MATCH":
            if self.state.status in (MatchStatus.COMPLETE, MatchStatus.ENDED):
                return None
            self.history.push(self.state)
            self.state.status = MatchStatus.ENDED
            self.state.winner = None
            self.state.paused = False
            return "MATCH ENDED"
        if self.state.paused:
            return None
        if command == "CHANGE_SERVER":
            return self.apply(f"TEAM_{self.state.game.serving.value}_SERVICE")
        if command == "END_GAME":
            if self.state.status != MatchStatus.PLAYING:
                return None
            scores = self.state.game.scores
            if scores["A"] == scores["B"]:
                return None
            self.history.push(self.state)
            winner = "A" if scores["A"] > scores["B"] else "B"
            self.state.games_won[winner] += 1
            self.state.completed_games.append(dict(scores))
            self.state.winner = winner
            self.state.status = (
                MatchStatus.COMPLETE
                if self.state.games_won[winner] >= self.settings.best_of // 2 + 1
                else MatchStatus.GAME_OVER
            )
            return (
                "MATCH WINNER"
                if self.state.status == MatchStatus.COMPLETE
                else "GAME WON"
            )
        if command == "NEXT_GAME":
            if self.state.status != MatchStatus.GAME_OVER:
                return None
            # This automatic transition belongs to the winning rally's undo entry.
            self.state.game_number += 1
            starter = Team(self.settings.first_serving_team)
            if self.state.game_number % 2 == 0:
                starter = starter.other
            self.state.game = self.new_state().game
            self.state.game.serving = starter
            self.state.status = MatchStatus.PLAYING
            self.state.winner = None
            return "NEXT GAME"
        if self.state.status != MatchStatus.PLAYING:
            return None
        parts = command.split("_")
        if len(parts) != 3 or parts[0] != "TEAM" or parts[1] not in ("A", "B"):
            return None
        team, action = Team(parts[1]), parts[2]
        if action == "TIMEOUT":
            if self.state.game.timeouts[team.value] >= self.settings.timeout_limit:
                return "NO TIMEOUTS REMAINING"
            self.history.push(self.state)
            self.state.game.timeouts[team.value] += 1
            return "TIMEOUT"
        if action not in ("POINT", "SERVICE"):
            return None
        self.history.push(self.state)
        winner = team if action == "POINT" else team.other
        result = self.strategies[self.settings.scoring_mode](
            self.state.game, winner, self.settings
        )
        scores = self.state.game.scores
        if (
            scores[winner.value] >= self.settings.points_to_win
            and scores[winner.value] - scores[winner.other.value]
            >= self.settings.win_by
        ):
            self.state.games_won[winner.value] += 1
            self.state.completed_games.append(dict(scores))
            self.state.winner = winner.value
            self.state.status = (
                MatchStatus.COMPLETE
                if self.state.games_won[winner.value] >= self.settings.best_of // 2 + 1
                else MatchStatus.GAME_OVER
            )
            return (
                "MATCH WINNER"
                if self.state.status == MatchStatus.COMPLETE
                else "GAME WON"
            )
        return result
