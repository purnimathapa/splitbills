-- Migration 014: group member roles (owner / admin / member)
ALTER TABLE trip_members
    ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'member';

-- Creator becomes owner where still a member
UPDATE trip_members tm
INNER JOIN trips t ON t.id = tm.trip_id
SET tm.role = 'owner'
WHERE tm.user_id = t.created_by;

-- One owner per group that still has none (legacy data)
UPDATE trip_members tm
INNER JOIN (
    SELECT MIN(tm2.id) AS member_row_id
    FROM trip_members tm2
    LEFT JOIN trip_members owners
        ON owners.trip_id = tm2.trip_id AND owners.role = 'owner'
    WHERE owners.id IS NULL
    GROUP BY tm2.trip_id
) pick ON pick.member_row_id = tm.id
SET tm.role = 'owner';
