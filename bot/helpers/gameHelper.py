from typing import List, Tuple
import gino
import enum

import app.game.models as m
from sqlalchemy import func


class GameState(enum.Enum):
    not_active = 0
    active = 1
    wait_question = 2
    wait_answer = 3
    error = 4


class GameHelper:
    themes_count = 3
    question_scores = range(1, 6)

    chat_id: int = 0
    db: gino = None

    def __init__(self, chat_id: int, db: gino) -> None:
        self.chat_id = chat_id
        self.db = db

    async def GetState(self) -> GameState:
        session = await self.GetSession()
        if not session:
            return GameState.not_active
        round = await self.GetRound(session=session)
        if not round:
            return GameState.error

        question = await self.getRoundQuestion(round=round)
        if question:
            return GameState.wait_answer
        return GameState.wait_question

    async def GetSession(self) -> m.Session:
        return (
            await m.Session.query.where(m.Session.chat_id == self.chat_id)
            .where(m.Session.status == m.SessionStatus.active)
            .gino.first()
        )

    async def GetRound(self, session: m.Session = None) -> m.Round:
        if not session:
            session = await self.GetSession()
        if not session:
            return None
        return (
            await m.Round.query.where(m.Round.session_id == session.id)
            .where(m.Round.status == m.RoundStatus.active)
            .gino.first()
        )

    async def getRoundQuestion(self, round: m.Round = None) -> m.RoundQuestion:
        if not round:
            round = await self.GetRound()
        if not round:
            return None
        return (
            await m.RoundQuestion.query.where(m.RoundQuestion.round_id == round.id)
            .where(m.RoundQuestion.status == m.RoundQuestionStatus.active)
            .gino.first()
        )

    async def getQuestion(
        self, rq: m.RoundQuestion = None
    ) -> Tuple[m.Question, m.RoundQuestion]:
        if not rq:
            rq = await self.getRoundQuestion()
        if not rq:
            return None
        return (
            await m.Question.query.where(m.Question.id == rq.question_id).gino.first(),
            rq,
        )

    async def GetThemes(self, round=None) -> List[m.Theme]:
        if not round:
            round = await self.GetRound()
        if not round:
            return []

        return (
            await m.Theme.query.select_from(
                m.Theme.join(m.ThemeRound, m.Theme.id == m.ThemeRound.theme_id)
            )
            .where(m.ThemeRound.round_id == round.id)
            .gino.all()
        )

    async def GetRandomThemes(self) -> List[m.Theme]:
        return (
            await m.Theme.query.order_by(func.random())
            .limit(self.themes_count)
            .gino.all()
        )

    async def GetRandomQuestion(self, score: int, theme_id: int) -> m.Question:
        return (
            await m.Question.query.where(m.Question.theme_id == theme_id)
            .where(m.Question.score == score)
            .order_by(func.random())
            .gino.first()
        )

    async def SetRandomThemes(self, round_id: int = None) -> List[m.Theme]:
        if not round_id:
            round = await self.GetRound()
            if not round:
                return None
            round_id = round.id

        themes = await self.GetRandomThemes()
        for theme in themes:
            await m.ThemeRound.create(
                round_id=round_id,
                theme_id=theme.id,
            )
        return themes

    async def startRound(self) -> m.Round:
        session = await self.GetSession()
        round = await self.GetRound(session=session)
        number = 1
        if round:
            number += round.number
            await round.update(
                status=m.RoundStatus.finished,
            ).apply()

        new_round = await self.createRound(number, session=session)

        return new_round

    async def createRound(self, number, session: m.Session = None) -> m.Round:
        if not session:
            session = await self.GetSession()

        new_round = await m.Round.create(
            session_id=session.id,
            number=number,
            status=m.RoundStatus.active,
        )

        await self.SetRandomThemes(round_id=new_round.id)

        return new_round

    async def createRoundQuestion(
        self, score: int, theme_id: int, round: m.Round = None
    ) -> Tuple[m.Question, m.RoundQuestion]:
        if not round:
            round = await self.GetRound()

        question = await self.GetRandomQuestion(score=score, theme_id=theme_id)
        rq = await m.RoundQuestion.create(
            question_id=question.id,
            round_id=round.id,
            status=m.RoundQuestionStatus.active,
        )

        return question, rq

    async def check_answer(self, answer: str, corect: str) -> m.AnswerStatus:
        # @todo: use mystem3
        if " ".join(answer.lower().split()) == " ".join(
            corect.lower().split()
        ):
            return m.AnswerStatus.correct
        return m.AnswerStatus.incorrect

    async def answerQuestion(
        self, answer: str, user_id: int, qrq: Tuple[m.Question, m.RoundQuestion] = None
    ) -> m.Answer:
        if not qrq:
            qrq = await self.getQuestion()

        question, rq = qrq

        is_correct = await self.check_answer(answer, question.correct_answer)

        answer = await m.Answer.create(
            status=is_correct,
            rq_id=rq.id,
            user_id=user_id,
        )

        if is_correct == is_correct.correct:
            await rq.update(status=m.RoundQuestionStatus.answered).apply()
            # @todo: add score

        return answer

    async def createSession(self) -> m.Session:
        return await m.Session.create(
            chat_id=self.chat_id,
            status=m.SessionStatus.active
        )

    async def getRoundQuestions(self, round: m.Round = None) -> List[m.Question]:
        if not round:
            round = await self.GetRound()
        print(round, round.id, round.session_id)
        
        return (
            await m.Question.query.select_from(
                m.Question.join(
                    m.RoundQuestion,
                    m.RoundQuestion.question_id == m.Question.id)
            )
            .where(m.RoundQuestion.round_id == round.id)
            .where(m.RoundQuestion.status == m.RoundQuestionStatus.answered)
            .gino.all()
        )

    async def table(self, round: m.Round = None):
        if not round:
            round = await self.GetRound()

        themes = await self.GetThemes()
        questions: List[m.Question] = await self.getRoundQuestions()

        table = dict()

        for theme in themes:
            table[theme.id] = {
                "title": theme.title,
                "id": theme.id,
                "answers": dict()
            }
            for score in self.question_scores:
                table[theme.id]["answers"][score] = False
            for question in questions:
                score = question.score
                table[theme.id]["answers"][score] = True

        return table
