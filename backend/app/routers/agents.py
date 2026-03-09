from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select
from app.db import get_session
from app.models import Agent, AgentSession, TaskAssignment, AgentMessage
from app.schemas import (
    AgentCreate,
    AgentUpdate,
    AgentRead,
    AgentSessionCreate,
    AgentSessionRead,
    TaskAssignmentCreate,
    TaskAssignmentUpdate,
    TaskAssignmentRead,
    AgentMessageSend,
    AgentMessageRead,
)

router = APIRouter(prefix="/agents", tags=["agents"])

def _agent_payload(agent: Agent) -> dict:
    return {
        "id": agent.id,
        "name": agent.name,
        "capabilities": agent.capabilities,
        "status": agent.status,
        "metadata": agent.agent_metadata,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
    }

def _message_payload(message: AgentMessage) -> dict:
    return {
        "id": message.id,
        "from_agent_id": message.from_agent_id,
        "to_agent_id": message.to_agent_id,
        "content": message.content,
        "message_type": message.message_type,
        "task_id": message.task_id,
        "read": message.read,
        "created_at": message.created_at,
    }


@router.post("", response_model=AgentRead)
def create_agent(payload: AgentCreate):
    with get_session() as session:
        agent = Agent(**payload.dict())
        session.add(agent)
        session.commit()
        session.refresh(agent)
        return _agent_payload(agent)


@router.get("", response_model=list[AgentRead])
def list_agents(status: Optional[str] = None):
    with get_session() as session:
        stmt = select(Agent)
        if status is not None:
            stmt = stmt.where(Agent.status == status)
        agents = session.exec(stmt.order_by(Agent.updated_at.desc())).all()
        return [_agent_payload(agent) for agent in agents]


@router.get("/{agent_id}", response_model=AgentRead)
def get_agent(agent_id: int):
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return _agent_payload(agent)


@router.patch("/{agent_id}", response_model=AgentRead)
def update_agent(agent_id: int, payload: AgentUpdate):
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        for k, v in payload.dict(exclude_unset=True).items():
            setattr(agent, k, v)
        agent.updated_at = datetime.utcnow()
        session.add(agent)
        session.commit()
        session.refresh(agent)
        return _agent_payload(agent)


@router.post("/{agent_id}/sessions", response_model=AgentSessionRead)
def start_session(agent_id: int, payload: AgentSessionCreate):
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        session_obj = AgentSession(
            agent_id=agent_id,
            context=payload.context,
        )
        agent.status = "online"
        session.add(session_obj)
        session.add(agent)
        session.commit()
        session.refresh(session_obj)
        return session_obj


@router.get("/{agent_id}/sessions", response_model=list[AgentSessionRead])
def list_sessions(agent_id: int):
    with get_session() as session:
        sessions = session.exec(
            select(AgentSession)
            .where(AgentSession.agent_id == agent_id)
            .order_by(AgentSession.started_at.desc())
        ).all()
        return sessions


@router.post("/{agent_id}/sessions/{session_id}/end", response_model=AgentSessionRead)
def end_session(agent_id: int, session_id: int):
    with get_session() as session:
        agent = session.get(Agent, agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        session_obj = session.get(AgentSession, session_id)
        if not session_obj or session_obj.agent_id != agent_id:
            raise HTTPException(status_code=404, detail="Session not found")
        session_obj.ended_at = datetime.utcnow()
        agent.status = "offline"
        session.add(session_obj)
        session.add(agent)
        session.commit()
        session.refresh(session_obj)
        return session_obj


@router.post("/assignments", response_model=TaskAssignmentRead)
def create_assignment(payload: TaskAssignmentCreate):
    with get_session() as session:
        assignment = TaskAssignment(**payload.dict())
        session.add(assignment)
        session.commit()
        session.refresh(assignment)
        return assignment


@router.get("/assignments", response_model=list[TaskAssignmentRead])
def list_assignments(agent_id: Optional[int] = None, task_id: Optional[int] = None, status: Optional[str] = None):
    with get_session() as session:
        stmt = select(TaskAssignment)
        if agent_id is not None:
            stmt = stmt.where(TaskAssignment.agent_id == agent_id)
        if task_id is not None:
            stmt = stmt.where(TaskAssignment.task_id == task_id)
        if status is not None:
            stmt = stmt.where(TaskAssignment.status == status)
        assignments = session.exec(stmt.order_by(TaskAssignment.updated_at.desc())).all()
        return assignments


@router.patch("/assignments/{assignment_id}", response_model=TaskAssignmentRead)
def update_assignment(assignment_id: int, payload: TaskAssignmentUpdate):
    with get_session() as session:
        assignment = session.get(TaskAssignment, assignment_id)
        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")
        for k, v in payload.dict(exclude_unset=True).items():
            setattr(assignment, k, v)
        assignment.updated_at = datetime.utcnow()
        session.add(assignment)
        session.commit()
        session.refresh(assignment)
        return assignment


@router.post("/{agent_id}/message", response_model=AgentMessageRead)
def send_message(agent_id: int, payload: AgentMessageSend, request: Request):
    with get_session() as session:
        from_agent = session.get(Agent, agent_id)
        if not from_agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        to_agent = session.get(Agent, payload.to_agent_id)
        if not to_agent:
            raise HTTPException(status_code=404, detail="Recipient agent not found")
        message = AgentMessage(
            from_agent_id=agent_id,
            to_agent_id=payload.to_agent_id,
            content=payload.content,
            message_type=payload.message_type,
            task_id=payload.task_id,
            read=False,
        )
        session.add(message)
        session.commit()
        session.refresh(message)

        mqtt_service = getattr(request.app.state, "mqtt", None)
        if mqtt_service is not None:
            mqtt_service.publish(
                f"agents/{payload.to_agent_id}/inbox",
                {
                    "id": message.id,
                    "from_agent_id": message.from_agent_id,
                    "to_agent_id": message.to_agent_id,
                    "content": message.content,
                    "message_type": message.message_type,
                    "task_id": message.task_id,
                    "timestamp": message.created_at.isoformat() + "Z",
                },
            )
        return _message_payload(message)


@router.get("/{agent_id}/messages", response_model=list[AgentMessageRead])
def list_messages(agent_id: int):
    with get_session() as session:
        stmt = (
            select(AgentMessage)
            .where((AgentMessage.from_agent_id == agent_id) | (AgentMessage.to_agent_id == agent_id))
            .order_by(AgentMessage.created_at.desc())
        )
        messages = session.exec(stmt).all()
        return [_message_payload(message) for message in messages]


@router.get("/{agent_id}/inbox", response_model=list[AgentMessageRead])
def get_inbox(agent_id: int):
    with get_session() as session:
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.to_agent_id == agent_id)
            .where(AgentMessage.read == False)  # noqa: E712
            .order_by(AgentMessage.created_at.asc())
        )
        messages = session.exec(stmt).all()
        for message in messages:
            message.read = True
            session.add(message)
        session.commit()
        return [_message_payload(message) for message in messages]
