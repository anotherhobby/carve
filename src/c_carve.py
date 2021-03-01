import networkx as nx
from networkx.readwrite import json_graph
import pylab as plt
import json
import sys
import os

from c_disco import c_disco
# from pprint import pprint


def run_test(G, c_context):
    targets = []
    for edge in list(G.edges):
        # edge example:  ('vpc-id', 'vpc-id')
        if c_context['VpcId'] in edge:
            for v in edge:
                if v != c_context['VpcId']:
                    targets.append(str(v))
    # get values
    for target in targets:
        PrivateEndpoint = G.nodes[target]['PrivateEndpoint']
        ApiGatewayUrl = G.nodes[target]['ApiGatewayUrl']
        # do some cool shit
    
    print(targets)


def network_diff(A, B):
    # compare peering both directions
    diff_peering(A, B)
    diff_vpcs(A, B)


def diff_peering(A, B, repeat=True):
    for edge in A.edges() - B.edges():
        print(f"DIFFERENCE DETECTED! \'{B.graph['Name']}\' contains a PEERING CONNECTION that \'{A.graph['Name']}\' does not:")
        print(f"#######################")
        print(A.nodes().data()[edge[0]])
        print(f"-------peered to-------")
        print(A.nodes().data()[edge[1]])
        print(f"#######################")
    if repeat:
        diff_peering(B, A, repeat=False)


def diff_vpcs(A, B, repeat=True):
    for node in A.nodes() - B.nodes():
        print(f"DIFF DETECTED! \'{B.graph['Name']}\' contains a VPC that \'{A.graph['Name']}\' does not:")
        print(f"#######################")
        print(A.nodes().data()[node])
        print(f"#######################")
    if repeat:
        diff_peering(B, A, repeat=False)



def export_visual(Graph, c_context):

    G = Graph

    # remove isolated nodes from graph
    if 'peers_only' in c_context:
        if c_context['peers_only'] == 'true':
            G.remove_nodes_from(list(nx.isolates(G)))

    print('drawing graph diagram')
    # print(f"/src/c_graphic_{G.graph['Name']}.png")

    options = {
        'node_color': 'blue',
        'node_size': 100,
        'font_size': 14,
        'width': 3,
        'with_labels': True,
    }

    plt.figure(G.graph['Name'],figsize=(24,24)) 

    nx.draw_circular(G, **options)

    # G = nx.cycle_graph(80)
    # pos = nx.circular_layout(G)

    # # default
    # plt.figure(1)
    # nx.draw(G,pos)

    # # smaller nodes and fonts
    # plt.figure(2)
    # nx.draw(G,pos,node_size=60,font_size=8) 

    # # larger figure size
    # plt.figure(3,figsize=(12,12)) 
    # nx.draw(G,pos)

    plt.savefig(f"/src/c_graphic_{G.graph['Name']}.png")



def draw_vpc(Graph, vpc):

    G = Graph

    print('drawing graph diagram')
    print(f"/src/c_graphic_{vpc}.png")

    # remove all edges without vpc
    for edge in G.edges:
        if vpc not in edge:
            G.remove_edge(edge[0], edge[1])

    # remove all nodes left without edges
    G.remove_nodes_from(list(nx.isolates(G)))


    options = {
        'node_color': 'blue',
        'node_size': 100,
        'font_size': 14,
        'width': 3,
        'with_labels': True,
    }

    plt.figure(vpc,figsize=(24,24)) 

    # nx.draw_circular(G, **options)
    # nx.draw_networkx(G, **options) # good for single
    # nx.draw_spectral(G, **options)
    # nx.draw_spring(G, **options) # similar to netoworkx also good
    nx.draw_shell(G, **options)

    plt.savefig(f"/src/c_graphic_{vpc}.png")



def load_graph(graph):
    try:
        with open(graph) as f:
            G = json_graph.node_link_graph(json.load(f))
            G.graph['Name'] = graph.split('/')[-1].split('.')[0]
            return G
    except Exception as e:
        print(f'error opening json_graph {json_graph}: {e}')
        return False


def save_graph(G, file_path):
    # save json data
    try:
        os.remove(file_path)

    with open(file_path, 'a') as f:
        json.dump(json_graph.node_link_data(G), f)
    

def main(c_context):


    # either load graph data for G from json, or generate dynamically
    if 'json_graph' in c_context:
        G = load_graph(c_context['json_graph'])
    else:
        G = False

    if not G:
        G = c_disco(c_context)

    if 'export_visual' in c_context:
        if c_context['export_visual'] == 'true':
            export_visual(G, c_context)

    if 'diff_graph' in c_context:
        D = load_graph(c_context['diff_graph'])
        if D:
            network_diff(G, D)
        else:
            print(f'cannot compare: diff_graph did not load')

    draw_vpc(G, c_context['VpcId'])




