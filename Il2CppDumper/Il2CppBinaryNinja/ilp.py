from binaryninja import *
import tree_sitter_languages
from tree_sitter import Node

parser = tree_sitter_languages.get_parser("cpp")


def first_or_else(lst, default):
    if len(lst) > 0:
        return lst[0]
    else:
        return default


def children_of_type(node: Node, ty: str) -> Node | None:
    return first_or_else([c for c in node.children if c.type == ty], None)


def handle_field_decl_list(fields: Node) -> dict[bytes, bool]:
    ret = dict()
    for field in fields.children:
        if field.type == "field_declaration" or field.type == "parameter_declaration":
            ty = field.children_by_field_name("type")[0]
            is_weak = first_or_else(field.children_by_field_name("declarator"), None)
            is_weak = (
                is_weak is None
                or is_weak.type == "pointer_declarator"
                or is_weak.type == "abstract_pointer_declarator"
            )
            if ty.type == "type_identifier":
                ret[ty.text] = (ret.get(ty.text) or False) or (not is_weak)
            elif ty.type == "struct_specifier" or ty.type == "union_specifier":
                fields = first_or_else(
                    [c for c in ty.children if c.type == "field_declaration_list"], None
                )
                if fields is not None:
                    ret.update(handle_field_decl_list(fields))
                else:
                    ident = [c for c in ty.children if c.type == "type_identifier"][
                        0
                    ].text
                    ret[ident] = (ret.get(ty.text) or False) or (not is_weak)
            elif ty.type == "primitive_type":
                pass
            elif ty.type == "sized_type_specifier":
                assert ty.child_count == 2, ty.text
                assert ty.children[1].type == "primitive_type", ty.text
            else:
                assert False, ty.text
    return ret


def find_ident(node: Node) -> bytes:
    for child in node.children:
        if child.type == "type_identifier":
            return child.text
        else:
            ident = find_ident(child)
            if ident is not None:
                return ident
    return None


def find_refs(node: Node) -> list[bytes]:
    ret = []
    for child in node.children:
        if child.type == "type_identifier":
            ret.append(child.text)
        else:
            ret.extend(find_refs(child))
    return ret


class TypedefInfo:
    name: str
    decl: str
    refs: dict[bytes, bool]  # type_ident -> is_strong
    type: str

    def __init__(self, name: str, decl: str, refs: dict[bytes, bool], type: str):
        self.name = name
        self.decl = decl
        self.refs = refs
        self.type = type


typedefs = dict[bytes, TypedefInfo]()


def load_file(filename: str):
    tree = parser.parse(open(filename, "rb").read())
    root = tree.root_node
    for node in root.children:
        if node.type == "struct_specifier" or node.type == "union_specifier":
            ident = [c for c in node.children if c.type == "type_identifier"][0].text
            base_class = first_or_else(
                [c for c in node.children if c.type == "base_class_clause"], None
            )
            fields = first_or_else(
                [c for c in node.children if c.type == "field_declaration_list"], None
            )
            refs = (
                handle_field_decl_list(fields)
                if fields is not None
                else dict[bytes, bool]()
            )
            if base_class is not None:
                base_class = [
                    c for c in base_class.children if c.type == "type_identifier"
                ][0].text
                refs[base_class] = True

            ty = "struct" if node.type == "struct_specifier" else "union"
            typedefs[ident] = TypedefInfo(ident, node.text, refs, ty)
        elif node.type == "type_definition":
            func_decl = children_of_type(node, "function_declarator")
            if func_decl is not None:
                paren_decl = children_of_type(func_decl, "parenthesized_declarator")
                ptr_decl = children_of_type(paren_decl, "pointer_declarator")
                ident = children_of_type(ptr_decl, "type_identifier").text

                param_list = children_of_type(func_decl, "parameter_list")
                refs = handle_field_decl_list(param_list)
                typedefs[ident] = TypedefInfo(ident, node.text, refs, "func")
            else:
                ident = find_ident(node)
                typedefs[ident] = TypedefInfo(ident, node.text, dict(), "type")

        elif node.type == ";":
            pass
        else:
            assert False, node.text


def build_struct(name: str, mark: set[str]) -> str:
    if name in mark:
        return ""

    ret = ""
    decl = typedefs[str.encode(name)]
    ret += f"// BEG {name}\n"
    for dep, is_strong in decl.refs.items():
        ty = typedefs[dep].type
        dep = dep.decode("utf-8")
        if is_strong:
            ret += build_struct(dep, mark)
        else:
            if dep not in mark:
                if ty != "struct" and ty != "union":
                    ty = ""
                ret += f"{ty} {dep};\n"
    if not name in mark:
        ret += f"{decl.decl.decode('utf-8')};\n"
        mark.add(name)
    for dep, is_strong in decl.refs.items():
        if not is_strong:
            ret += build_struct(dep.decode("utf-8"), mark)
    ret += f"// END {name}\n"
    return ret
