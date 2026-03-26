"""
providers/base.py
Defines the contract every email provider must fulfil.
The classifier and router never import from m365.py or gmail.py directly —
they only ever see a StandardEmail, keeping providers fully swappable.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class StandardEmail:
    """
    Normalised email object — identical shape regardless of which provider
    (M365, Gmail, mock) produced it.
    """
    id:        str                    # provider-native message ID (used to mark as read)
    subject:   str
    body:      str                    # plain-text body (HTML stripped by the provider)
    sender:    str                    # e.g. "alice@company.com"
    timestamp: datetime
    raw:       dict = field(default_factory=dict)   # original API response, for debugging


class BaseEmailProvider(ABC):
    """
    Abstract provider.  Implement this for M365, Gmail, or any future source.
    """

    @abstractmethod
    def fetch_new_emails(self) -> list[StandardEmail]:
        """
        Return all unread emails that have arrived since the last poll.
        Implementations must NOT mark emails as read here — that happens
        in mark_as_processed() so failures don't silently drop messages.
        """
        ...

    @abstractmethod
    def mark_as_processed(self, email_id: str) -> None:
        """
        Mark a single email as read / processed so it isn't fetched again.
        Called only after the message has been successfully routed or queued.
        """
        ...

    @abstractmethod
    def forward_email(self, email: StandardEmail, to_address: str) -> None:
        """
        Forward the email to the given address on behalf of the shared mailbox.
        """
        ...

    @abstractmethod
    def forward_for_review(
        self,
        email: "StandardEmail",
        to_address: str,
        context_header: str,
    ) -> None:
        """
        Forward the email to the human-review inbox, prepending a
        plain-text context block that explains why it wasn't auto-routed
        and lists the department addresses so the reviewer can act immediately.
        """
        ...
