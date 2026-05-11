from app.agents.base import BaseAgent, _CriticAgent


class OutreachAgent(BaseAgent):
    slug = "outreach-agent"
    name = "Outreach Agent"
    description = (
        "Drafts and queues personalised outreach emails after SDR Researcher completes."
    )
    model = "gpt-4o-mini"


class VoiceCriticAgent(_CriticAgent):
    slug = "voice-critic"
    name = "Voice Critic"
    description = (
        "Evaluates outbound drafts against the Frogslayer voice profile. Used as "
        "an internal critique step inside the Outreach chain."
    )
    model = "gpt-4o-mini"


class AccuracyCriticAgent(_CriticAgent):
    slug = "accuracy-critic"
    name = "Accuracy Critic"
    description = (
        "Cross-checks outbound drafts for factual claims that aren't supported by "
        "the upstream context. Used as an internal critique step inside the Outreach chain."
    )
    model = "gpt-4o-mini"
