def calculate_settlement(expenses, members):
    """Net balances from expenses, then greedy payment matching.

    Each expense credits the payer and debits participants using stored
    expense_splits when present; otherwise falls back to an equal split of
    that expense among trip members.
    """

    if not members:
        return []

    balances = {member.name: 0.0 for member in members}
    member_count = len(members)

    for expense in expenses:
        amount = expense.amount or 0
        if amount <= 0:
            continue

        balances[expense.payer.name] += amount

        splits = expense.splits if expense.splits is not None else []
        if splits:
            user_by_id = {member.id: member for member in members}
            for split in splits:
                user = split.user or user_by_id.get(split.user_id)
                if user is None:
                    continue
                balances[user.name] -= split.amount_owed or 0
        else:
            share = amount / member_count
            for member in members:
                balances[member.name] -= share

    debtors = []
    creditors = []

    for user, amount in balances.items():
        if amount < -0.009:
            debtors.append([user, abs(amount)])
        elif amount > 0.009:
            creditors.append([user, amount])

    debtors.sort(key=lambda row: row[1], reverse=True)
    creditors.sort(key=lambda row: row[1], reverse=True)

    result = []
    i = 0
    j = 0

    while i < len(debtors) and j < len(creditors):
        pay = min(debtors[i][1], creditors[j][1])

        result.append(
            {
                "from": debtors[i][0],
                "to": creditors[j][0],
                "amount": round(pay, 2),
            }
        )

        debtors[i][1] -= pay
        creditors[j][1] -= pay

        if debtors[i][1] < 0.01:
            i += 1

        if creditors[j][1] < 0.01:
            j += 1

    return result
