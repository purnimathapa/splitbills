def calculate_settlement(expenses, members):

    total_spent = 0

    for expense in expenses:
        total_spent += expense.amount

    share = total_spent / len(members)

    balances = {}

    for member in members:
        balances[member.name] = -share

    for expense in expenses:
        balances[expense.payer.name] += expense.amount

    debtors = []
    creditors = []

    for user, amount in balances.items():

        if amount < 0:
            debtors.append(
                [user, abs(amount)]
            )

        elif amount > 0:
            creditors.append(
                [user, amount]
            )

    result = []

    i = 0
    j = 0

    while i < len(debtors) and j < len(creditors):

        pay = min(
            debtors[i][1],
            creditors[j][1]
        )

        result.append(
            {
                "from": debtors[i][0],
                "to": creditors[j][0],
                "amount": round(pay, 2)
            }
        )

        debtors[i][1] -= pay
        creditors[j][1] -= pay

        if debtors[i][1] < 0.01:
            i += 1

        if creditors[j][1] < 0.01:
            j += 1

    return result