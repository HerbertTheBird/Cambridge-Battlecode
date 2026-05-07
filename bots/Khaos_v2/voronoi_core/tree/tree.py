from voronoi_core.nodes import Arc, Breakpoint
from voronoi_core.tree.node import Node


class Tree:
    """
    Self-balancing Binary Search Tree.
    """

    @staticmethod
    def find(root: Node, key, **kwargs):

        node = root
        while node is not None:
            node_key = node.get_key(**kwargs)
            if key == node_key:
                break
            elif key < node_key:
                node = node._left
            else:
                node = node._right

        # Return node, None if not found
        return node

    @staticmethod
    def find_value(root: Node, query: Node, compare=lambda x, y: x == y, sweep_line=None):
        """
        Find an item using a query node and a comparison function.

        :param root: (Node) The root to start searching from
        :param query: The query
        :param compare: (lambda) Lambda expression to compare the node against the query. Will be called as
        compare(node.data, query.data).
        :param sweep_line: Sweep line position passed through to get_key().
        :return: (Node or None) Returns the node that corresponds to the query or None
        """
        key = query.get_key(sweep_line=sweep_line)
        node = root
        while node is not None:
            node_key = node.get_key(sweep_line=sweep_line)
            if key == node_key:

                if compare(node.data, query.data):
                    return node

                left = Tree.find_value(node._left, query, compare, sweep_line=sweep_line)
                if left is None:
                    right = Tree.find_value(node._right, query, compare, sweep_line=sweep_line)
                    return right

                return left

            elif key < node_key:
                # Normally, the three should go left and find the correct value there,
                # but due to rounding errors, it sometimes takes the wrong turn. So if the left
                # branch doesn't get a result, we try the other branch.
                return Tree.find_value(node._left, query, compare, sweep_line=sweep_line) or \
                       Tree.find_value(node._right, query, compare, sweep_line=sweep_line)
            else:
                # Normally, the three should go right and find the correct value there,
                # but due to rounding errors, it sometimes takes the wrong turn. So if the right
                # branch doesn't get a result, we try the other branch.
                return Tree.find_value(node._right, query, compare, sweep_line=sweep_line) or \
                       Tree.find_value(node._left, query, compare, sweep_line=sweep_line)

    @staticmethod
    def find_leaf_node(root: Node, key, sweep_line=None):
        """
        Follows a path downward between the internal nodes using the key until it
        reaches a leaf node. If it is unclear which path to take, the left path is
        taken.

        :param root: (Node) The root of the (sub)tree to travel down
        :param key: The key to use to determine the path
        :param sweep_line: Sweep line position passed through to get_key().
        :return: (Node) The node found at the end of the journey
        """

        node = root
        while node is not None:
            left = node._left
            right = node._right

            # is_leaf() inlined: avoid a method call per iteration.
            if left is None and right is None:
                return node

            node_key = node.get_key(sweep_line=sweep_line)

            # If we found the key, we choose a direction
            if key == node_key:

                # We take the left path if possible
                if left is not None:
                    return left.maximum()

                # Otherwise we take the right path
                return right.minimum()

            # Normal binary search
            elif key < node_key:
                node = left
            else:
                node = right

        # Return node, None if not found
        return node

    @staticmethod
    def insert(root: Node, node: Node, **kwargs):

        # Get keys once
        node_key = node.get_key(**kwargs) if node is not None else None
        root_key = root.get_key(**kwargs) if root is not None else None

        # Binary Search Tree insert
        if root is None:
            return node
        elif node_key < root_key:
            root.left = Tree.insert(root.left, node, **kwargs)
        else:
            root.right = Tree.insert(root.right, node, **kwargs)

        # Update the height of the ancestor node
        root.update_height()

        # If the node is unbalanced, then try out the 4 cases
        balance = root.balance
        # root = Tree.balance(root)

        # Case 1 - Left Left
        left = root._left
        right = root._right

        if balance > 1 and node_key < left.get_key(**kwargs):
            return Tree.rotate_right(root)

        # Case 2 - Right Right
        if balance < -1 and node_key > right.get_key(**kwargs):
            return Tree.rotate_left(root)

        # Case 3 - Left Right
        if balance > 1 and node_key > left.get_key(**kwargs):
            root.left = Tree.rotate_left(left)
            return Tree.rotate_right(root)

        # Case 4 - Right Left
        if balance < -1 and node_key < right.get_key(**kwargs):
            root.right = Tree.rotate_right(right)
            return Tree.rotate_left(root)

        return root

    @staticmethod
    def delete(root: Node, key: int, **kwargs):

        if root is None:
            return root

        elif key < root.get_key():
            root.left = Tree.delete(root.left, key)

        elif key > root.get_key():
            root.right = Tree.delete(root.right, key)

        else:
            if root.left is None:
                return root.right

            elif root.right is None:
                return root.left

            temp = root.right.minimum()
            root.data = temp.data
            root.right = Tree.delete(root.right, temp.value.get_key(**kwargs))

        # If the tree has only one node, simply return it
        if root is None:
            return root

        # Update the height of the ancestor node
        root.update_height()

        # Balance the tree
        root = Tree.balance(root)

        return root

    @staticmethod
    def balance_and_propagate(node):
        """
        Walks up the tree recursively to rebalance all nodes, until it reaches the new root.

        :param node: The starting point, everything below this point is assumed to be balanced.
        :return: The root of the balanced tree
        """

        current = node
        while True:
            current = Tree.balance(current)
            if current.parent is None:
                return current
            current = current.parent

    @staticmethod
    def balance(node):
        """
        Make the three balanced if it is unbalanced.
        :param node: (Node) The root node of the tree to balance
        :return: (Node) The new root of the sub tree
        """

        # If the node is unbalanced, then try out the 4 cases.
        # The four original cases are mutually exclusive on the sign of balance,
        # so we group by sign and evaluate each child's balance at most once.

        balance = node.balance

        if balance > 1:
            left = node._left
            # Case 1 - Left Left  (left.balance >= 0)
            # Case 3 - Left Right (left.balance < 0)
            if left.balance >= 0:
                return Tree.rotate_right(node)
            # rotate_left already wires the new root into node._left via the
            # grandparent reparent step, so the explicit `node.left = ...`
            # assignment would just redundantly set parent/_left again.
            Tree.rotate_left(left)
            return Tree.rotate_right(node)

        if balance < -1:
            right = node._right
            # Case 2 - Right Right (right.balance <= 0)
            # Case 4 - Right Left  (right.balance > 0)
            if right.balance <= 0:
                return Tree.rotate_left(node)
            Tree.rotate_right(right)
            return Tree.rotate_left(node)

        return node

    @staticmethod
    def rotate_left(z):
        r"""
        Rotate tree to the left.

        # T1, T2, T3 and T4 are subtrees.
        #     z                               y
        #    / \                            /  \
        #   T1   y     Left Rotate(z)      z     x
        #       / \   - - - - - - - ->   / \    / \
        #      T2  x                    T1  T2 T3  T4
        #         / \
        #       T3  T4

        :param z: (Node) The root of the sub tree
        :return: (Node) The new root of the sub tree
        """
        grandparent = z.parent
        y = z._right
        T2 = y._left

        # Appoint new parent to root of sub tree
        y.parent = grandparent

        # And point the parent back. We bypass the .left/.right setters since
        # they would redundantly reassign y.parent (already set above).
        if grandparent is not None:
            if grandparent._left is z:
                grandparent._left = y
            else:
                grandparent._right = y

        # Perform rotation. Bypass setters and manage parent pointers directly.
        y._left = z
        z.parent = y
        z._right = T2
        if T2 is not None:
            T2.parent = z

        # Update heights (z has to be updated first, because it is a child of y)
        z.update_height()
        y.update_height()

        # Return the new root
        return y

    @staticmethod
    def rotate_right(z):
        r"""
        Rotate tree to the right.

        # T1, T2, T3 and T4 are subtrees.
        #          z                                      y
        #         / \                                   /   \
        #        y   T4      Right Rotate (z)          x      z
        #       / \          - - - - - - - - ->      /  \    /  \
        #      x   T3                               T1  T2  T3  T4
        #     / \
        #   T1   T2

        :param z: (Node) The root of the sub tree
        :return: (Node) The new root of the sub tree
        """
        grandparent = z.parent
        y = z._left
        T3 = y._right

        # Appoint new parent to root of sub tree
        y.parent = grandparent

        # And point the parent back. Bypass the .left/.right setters since they
        # would redundantly reassign y.parent (already set above).
        if grandparent is not None:
            if grandparent._left is z:
                grandparent._left = y
            else:
                grandparent._right = y

        # Perform rotation. Bypass setters and manage parent pointers directly.
        y._right = z
        z.parent = y
        z._left = T3
        if T3 is not None:
            T3.parent = z

        # Update heights (z has to be updated first, because it is a child of y)
        z.update_height()
        y.update_height()

        # Return the new root
        return y

    @staticmethod
    def get_leaves(root: Node, leaves=None):
        if leaves is None:
            leaves = []

        # Base case
        if root.is_leaf():
            leaves.append(root)
            return leaves

        # Step
        if root.left is not None:
            leaves += Tree.get_leaves(root.left, None)
        if root.right is not None:
            leaves += Tree.get_leaves(root.right, None)
        return leaves
